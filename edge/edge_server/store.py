"""The queue and the registry, on disk in SQLite.

WHY SQLITE AND NOT A DICT

The V1/V2 prototype kept dispatched runs in a module-level dict and said so in
its README: two uvicorn workers each get their own copy, so the idempotency
guarantee quietly disappears. That was an acceptable admission when the dict
only recorded what had happened. Here the queue *is* the system — it decides
which of three test servers runs a job, and whether a job survives a restart —
so an in-process structure would be the single point where correctness is lost.

SQLite buys three things that matter and cost about a hundred lines:

  * ATOMIC CLAIM. Three test servers long-poll the same queue. Exactly one must
    get any given job. `BEGIN IMMEDIATE` plus a conditional UPDATE gives that
    with no lock of our own to get wrong.
  * LEASES. A test server that dies mid-run must not take the job with it. The
    claim carries an expiry; a lease that is not renewed is requeued.
  * DURABILITY. The edge restarts and the queue is still there.

WHERE THE LEASE WINDOW IS A REAL DESIGN CHOICE

Requeueing is at-least-once, and at-least-once means a slow-but-alive test
server can have its job handed to a second one — the same suite running twice
against `staging`. The heartbeat interval (30s) and the lease (120s) are set so
a live test server renews four times before its lease could expire; anything
that misses four consecutive heartbeats is not slow, it is gone. `attempts` is
capped so a job that kills its runner cannot kill all three in turn.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS runners (
    runner_id   TEXT PRIMARY KEY,
    public_key  TEXT NOT NULL,
    labels      TEXT NOT NULL DEFAULT '',
    enrolled_at REAL NOT NULL,
    last_seen   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    product       TEXT NOT NULL,
    server        TEXT NOT NULL,
    select_expr   TEXT,
    marker        TEXT,
    slack_channel TEXT NOT NULL,
    slack_user    TEXT NOT NULL,
    created_at    REAL NOT NULL,
    state         TEXT NOT NULL,
    runner_id     TEXT,
    lease_expires REAL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    started_at    REAL,
    finished_at   REAL,
    exit_code     INTEGER,
    passed        INTEGER,
    failed        INTEGER,
    skipped       INTEGER,
    duration      REAL,
    summary       TEXT
);

CREATE INDEX IF NOT EXISTS jobs_state_created ON jobs (state, created_at);
"""

#: Every state a job can be in. `queued` and `claimed` are the only two a
#: reaper ever moves between; the rest are terminal or runner-driven.
QUEUED, CLAIMED, RUNNING, DONE, FAILED, ABANDONED = (
    "queued", "claimed", "running", "done", "failed", "abandoned",
)


@dataclass(slots=True)
class Job:
    id: str
    product: str
    server: str
    select_expr: str | None
    marker: str | None
    slack_channel: str
    slack_user: str

    def as_dispatch(self) -> dict[str, Any]:
        """The shape handed to a test server. Deliberately minimal.

        No Slack tokens, no internal hostnames, no config — just what is needed
        to run a suite and say where the answer goes. A job payload is the one
        thing that crosses from the public edge onto an internal machine, so
        the less it carries the less a forged one could do.
        """
        return {
            "job_id": self.id,
            "product": self.product,
            "server": self.server,
            "select": self.select_expr or "",
            "marker": self.marker or "",
            "slack_channel": self.slack_channel,
            "slack_user": self.slack_user,
        }


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            # WAL so a long-polling reader never blocks the writer that is
            # trying to enqueue a job from the Slack handler.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    # ── runners ──────────────────────────────────────────────────────────────

    def enrol(self, runner_id: str, public_key: str, labels: Iterable[str], now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runners (runner_id, public_key, labels, enrolled_at, last_seen) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(runner_id) DO UPDATE SET public_key=excluded.public_key, "
                "labels=excluded.labels, last_seen=excluded.last_seen",
                (runner_id, public_key, ",".join(sorted(labels)), now, now),
            )

    def runner(self, runner_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM runners WHERE runner_id=?", (runner_id,)
            ).fetchone()

    def touch(self, runner_id: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("UPDATE runners SET last_seen=? WHERE runner_id=?", (now, runner_id))

    def runners(self, offline_after: float, now: float | None = None) -> list[dict[str, Any]]:
        now = time.time() if now is None else now
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM runners ORDER BY runner_id").fetchall()
        return [
            {
                "runner_id": r["runner_id"],
                "labels": [x for x in r["labels"].split(",") if x],
                "enrolled_at": r["enrolled_at"],
                "last_seen": r["last_seen"],
                "seconds_since_seen": round(now - r["last_seen"], 1),
                "state": "online" if (now - r["last_seen"]) <= offline_after else "offline",
            }
            for r in rows
        ]

    def online(self, offline_after: float, now: float | None = None) -> list[dict[str, Any]]:
        return [r for r in self.runners(offline_after, now) if r["state"] == "online"]

    # ── jobs ─────────────────────────────────────────────────────────────────

    def enqueue(self, job: Job, now: float | None = None) -> bool:
        """Add a job. Returns False if this id is already known.

        The id is the correlation id derived from Slack's `trigger_id`, so a
        Slack retry lands on the same row and this returns False — which is the
        whole idempotency mechanism, enforced by a PRIMARY KEY rather than by
        remembering to check.
        """
        now = time.time() if now is None else now
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO jobs "
                "(id, product, server, select_expr, marker, slack_channel, slack_user, created_at, state) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (job.id, job.product, job.server, job.select_expr, job.marker,
                 job.slack_channel, job.slack_user, now, QUEUED),
            )
            return cur.rowcount == 1

    def claim(
        self,
        runner_id: str,
        labels: Iterable[str],
        lease_seconds: float,
        max_attempts: int,
        now: float | None = None,
    ) -> Job | None:
        """Hand exactly one queued job to this test server, or None.

        BEGIN IMMEDIATE takes the write lock up front. Without it two claims
        can both read the same `queued` row before either writes, and SQLite
        answers the loser with SQLITE_BUSY on COMMIT rather than at SELECT —
        which is a race that only shows up under the concurrency you built the
        three-runner harness to create.
        """
        now = time.time() if now is None else now
        wanted = {x for x in labels if x}
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._reap(conn, now, max_attempts)
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE state=? ORDER BY created_at ASC", (QUEUED,)
                ).fetchall()
                for row in rows:
                    # A test server with no labels is a general-purpose one and
                    # takes anything. A labelled one takes only jobs for an
                    # environment it declares — that is the "send it to the
                    # right machine" routing, and the default (no labels) is
                    # the shared pool the queueing test needs.
                    if wanted and row["server"] not in wanted:
                        continue
                    cur = conn.execute(
                        "UPDATE jobs SET state=?, runner_id=?, lease_expires=?, attempts=attempts+1 "
                        "WHERE id=? AND state=?",
                        (CLAIMED, runner_id, now + lease_seconds, row["id"], QUEUED),
                    )
                    if cur.rowcount == 1:
                        conn.execute("COMMIT")
                        return Job(
                            id=row["id"], product=row["product"], server=row["server"],
                            select_expr=row["select_expr"], marker=row["marker"],
                            slack_channel=row["slack_channel"], slack_user=row["slack_user"],
                        )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return None

    def mark_running(self, job_id: str, runner_id: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET state=?, started_at=? WHERE id=? AND runner_id=? AND state=?",
                (RUNNING, now, job_id, runner_id, CLAIMED),
            )
            return cur.rowcount == 1

    def finish(
        self,
        job_id: str,
        runner_id: str,
        *,
        exit_code: int,
        passed: int,
        failed: int,
        skipped: int,
        duration: float,
        summary: str,
        now: float | None = None,
    ) -> bool:
        """Record a result — but only from the test server that holds the job.

        The `runner_id=?` in the WHERE clause is the security control, not an
        optimisation. Without it any enrolled test server could post a result
        for a job it was never given, and since the test servers are what talk
        to Slack, a forged result is a forged message in the channel.
        """
        now = time.time() if now is None else now
        state = DONE if exit_code == 0 else FAILED
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET state=?, finished_at=?, exit_code=?, passed=?, failed=?, "
                "skipped=?, duration=?, summary=?, lease_expires=NULL "
                "WHERE id=? AND runner_id=? AND state IN (?,?)",
                (state, now, exit_code, passed, failed, skipped, duration, summary,
                 job_id, runner_id, CLAIMED, RUNNING),
            )
            return cur.rowcount == 1

    def renew(self, runner_id: str, lease_seconds: float, now: float | None = None) -> int:
        """Extend the lease on everything this test server currently holds."""
        now = time.time() if now is None else now
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE jobs SET lease_expires=? WHERE runner_id=? AND state IN (?,?)",
                (now + lease_seconds, runner_id, CLAIMED, RUNNING),
            )
            return cur.rowcount

    def _reap(self, conn: sqlite3.Connection, now: float, max_attempts: int) -> None:
        """Requeue anything whose lease ran out; abandon it if it has had enough goes.

        The attempts cap is what stops a job that crashes its host from being
        handed to each of the three in turn and taking the whole fleet down —
        a failure mode that looks like "the test servers are unstable" and is
        really one poisonous job.
        """
        conn.execute(
            "UPDATE jobs SET state=?, runner_id=NULL, lease_expires=NULL "
            "WHERE state IN (?,?) AND lease_expires IS NOT NULL AND lease_expires < ? "
            "AND attempts < ?",
            (QUEUED, CLAIMED, RUNNING, now, max_attempts),
        )
        conn.execute(
            "UPDATE jobs SET state=?, lease_expires=NULL, "
            "summary='lease expired and out of attempts' "
            "WHERE state IN (?,?) AND lease_expires IS NOT NULL AND lease_expires < ? "
            "AND attempts >= ?",
            (ABANDONED, CLAIMED, RUNNING, now, max_attempts),
        )

    def reap(self, max_attempts: int, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._reap(conn, now, max_attempts)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def last_for(self, product: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE product=? ORDER BY created_at DESC LIMIT 1", (product,)
            ).fetchone()
        return dict(row) if row else None

    def counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT state, COUNT(*) n FROM jobs GROUP BY state").fetchall()
        return {r["state"]: r["n"] for r in rows}


__all__ = ["Store", "Job", "QUEUED", "CLAIMED", "RUNNING", "DONE", "FAILED", "ABANDONED", "asdict"]

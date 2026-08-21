"""The queue: idempotency, exactly-once claim, leases, and result ownership.

These are the properties the three-test-server harness exists to demonstrate at
runtime. Demonstrating is not proving — a race that shows up once in fifty runs
looks like a green harness — so they are pinned here where the timing can be
controlled.
"""

from __future__ import annotations

import threading

import pytest

from edge_server.store import ABANDONED, CLAIMED, DONE, FAILED, QUEUED, RUNNING, Job, Store

pytestmark = pytest.mark.unit


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "edge.db")


def make_job(job_id: str = "job-1", product: str = "webapp", server: str = "staging") -> Job:
    return Job(
        id=job_id, product=product, server=server, select_expr=None, marker=None,
        slack_channel="#testing", slack_user="U1",
    )


# ── idempotency ──────────────────────────────────────────────────────────────

def test_the_same_job_id_is_only_queued_once(store: Store) -> None:
    """Slack retries anything slow. The PRIMARY KEY is the whole mechanism."""
    assert store.enqueue(make_job()) is True
    assert store.enqueue(make_job()) is False
    assert store.counts() == {QUEUED: 1}


# ── exactly-once claim ───────────────────────────────────────────────────────

def test_one_job_goes_to_exactly_one_test_server(store: Store) -> None:
    store.enqueue(make_job())

    claims = [store.claim(f"runner-{n}", [], 60, 3) for n in range(1, 4)]
    won = [c for c in claims if c is not None]

    assert len(won) == 1, "a job must never be handed to two machines"
    assert won[0].id == "job-1"


def test_concurrent_claims_do_not_double_assign(store: Store) -> None:
    """The race the harness creates, run deliberately.

    Ten threads against ten jobs: every job must be claimed exactly once, and
    no thread may see the same job as another. Without BEGIN IMMEDIATE two
    claims can both read the same queued row before either writes.
    """
    for n in range(10):
        store.enqueue(make_job(f"job-{n}"))

    results: list[str] = []
    lock = threading.Lock()

    def grab(runner: str) -> None:
        while True:
            job = store.claim(runner, [], 60, 3)
            if job is None:
                return
            with lock:
                results.append(job.id)

    threads = [threading.Thread(target=grab, args=(f"runner-{n}",)) for n in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 10
    assert len(set(results)) == 10, "a job was claimed twice"


def test_an_empty_queue_returns_nothing_rather_than_blocking(store: Store) -> None:
    assert store.claim("runner-1", [], 60, 3) is None


# ── routing ──────────────────────────────────────────────────────────────────

def test_a_labelled_server_only_takes_jobs_for_its_environments(store: Store) -> None:
    store.enqueue(make_job("job-prod-ish", server="staging"))

    assert store.claim("dev-only", ["dev"], 60, 3) is None
    assert store.claim("staging-box", ["staging"], 60, 3) is not None


def test_an_unlabelled_server_takes_anything(store: Store) -> None:
    """Empty labels is the shared pool — the default, and what the harness uses."""
    store.enqueue(make_job(server="dev"))

    assert store.claim("general", [], 60, 3) is not None


# ── leases ───────────────────────────────────────────────────────────────────

def test_an_expired_lease_returns_the_job_to_the_queue(store: Store) -> None:
    """A test server that dies mid-run must not take the job with it."""
    store.enqueue(make_job())
    claimed = store.claim("runner-1", [], lease_seconds=10, max_attempts=3, now=1_000.0)
    assert claimed is not None

    # 1_011 is past the lease; a second server asks for work.
    recovered = store.claim("runner-2", [], lease_seconds=10, max_attempts=3, now=1_011.0)

    assert recovered is not None and recovered.id == "job-1"
    assert store.job("job-1")["runner_id"] == "runner-2"


def test_a_heartbeat_keeps_a_running_job_from_being_stolen(store: Store) -> None:
    """The failure this prevents: a healthy server declared dead because it was busy."""
    store.enqueue(make_job())
    store.claim("runner-1", [], lease_seconds=10, max_attempts=3, now=1_000.0)
    store.renew("runner-1", lease_seconds=10, now=1_008.0)

    assert store.claim("runner-2", [], lease_seconds=10, max_attempts=3, now=1_011.0) is None


def test_a_job_that_keeps_killing_its_runner_is_abandoned(store: Store) -> None:
    """Otherwise one poisonous job takes down all three machines in turn."""
    store.enqueue(make_job())
    store.claim("runner-1", [], lease_seconds=10, max_attempts=2, now=1_000.0)
    store.claim("runner-2", [], lease_seconds=10, max_attempts=2, now=1_011.0)
    store.reap(max_attempts=2, now=1_022.0)

    assert store.job("job-1")["state"] == ABANDONED
    assert store.claim("runner-3", [], 10, 2, now=1_030.0) is None


# ── ownership ────────────────────────────────────────────────────────────────

def test_only_the_holder_can_report_a_result(store: Store) -> None:
    """A forged result is a forged Slack message — the test servers do the posting."""
    store.enqueue(make_job())
    store.claim("runner-1", [], 60, 3)

    stolen = store.finish("job-1", "runner-2", exit_code=0, passed=99, failed=0,
                          skipped=0, duration=1.0, summary="")
    assert stolen is False
    assert store.job("job-1")["passed"] is None

    real = store.finish("job-1", "runner-1", exit_code=0, passed=3, failed=0,
                        skipped=1, duration=1.0, summary="")
    assert real is True
    assert store.job("job-1")["state"] == DONE


def test_a_nonzero_exit_records_a_failure(store: Store) -> None:
    store.enqueue(make_job())
    store.claim("runner-1", [], 60, 3)
    store.finish("job-1", "runner-1", exit_code=1, passed=2, failed=1,
                 skipped=0, duration=2.0, summary="webapp::test_x")

    assert store.job("job-1")["state"] == FAILED


def test_a_result_for_an_unclaimed_job_is_refused(store: Store) -> None:
    store.enqueue(make_job())

    assert store.finish("job-1", "runner-1", exit_code=0, passed=1, failed=0,
                        skipped=0, duration=1.0, summary="") is False


def test_started_moves_a_claimed_job_to_running(store: Store) -> None:
    store.enqueue(make_job())
    store.claim("runner-1", [], 60, 3)

    assert store.mark_running("job-1", "runner-1") is True
    assert store.job("job-1")["state"] == RUNNING
    assert store.mark_running("job-1", "runner-2") is False


# ── registry ─────────────────────────────────────────────────────────────────

def test_a_server_that_stops_heartbeating_is_reported_offline(store: Store) -> None:
    store.enrol("runner-1", "pubkey", [], now=1_000.0)

    assert store.runners(offline_after=90, now=1_050.0)[0]["state"] == "online"
    assert store.runners(offline_after=90, now=1_200.0)[0]["state"] == "offline"
    assert store.online(offline_after=90, now=1_200.0) == []


def test_re_enrolment_updates_the_key_and_last_seen(store: Store) -> None:
    """A restart is normal and must work without an operator touching anything."""
    store.enrol("runner-1", "key-a", ["dev"], now=1_000.0)
    store.enrol("runner-1", "key-b", ["staging"], now=2_000.0)

    row = store.runner("runner-1")
    assert row["public_key"] == "key-b"
    assert row["labels"] == "staging"
    assert len(store.runners(90, now=2_000.0)) == 1


def test_the_queue_survives_reopening_the_database(tmp_path) -> None:
    """The edge restarting must not lose queued work."""
    path = tmp_path / "edge.db"
    Store(path).enqueue(make_job())

    assert Store(path).claim("runner-1", [], 60, 3) is not None

"""Every environment variable the edge reads, in one place."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from slack_runtests.slack import DEFAULT_CHANNEL


def _csv(name: str, default: str = "") -> frozenset[str]:
    raw = os.environ.get(name, default)
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


@dataclass(slots=True)
class EdgeConfig:
    # ── Slack side (the public door) ─────────────────────────────────────────
    signing_secret: str = field(default_factory=lambda: os.environ.get("SLACK_SIGNING_SECRET", ""))
    default_channel: str = field(
        default_factory=lambda: os.environ.get("SLACK_DEFAULT_CHANNEL", DEFAULT_CHANNEL)
    )
    allowed_team: str = field(default_factory=lambda: os.environ.get("SLACK_TEAM_ID", ""))
    allowed_channels: frozenset[str] = field(default_factory=lambda: _csv("RUNTESTS_CHANNELS"))
    allowed_users: frozenset[str] = field(default_factory=lambda: _csv("RUNTESTS_USERS"))

    # ── test-server side (the second public door) ────────────────────────────
    db_path: str = field(default_factory=lambda: os.environ.get("EDGE_DB_PATH", "data/edge.db"))
    key_path: str = field(
        default_factory=lambda: os.environ.get("EDGE_KEY_PATH", "keys/edge_ed25519.pem")
    )
    #: Directory of pre-authorised public keys, one file per test server named
    #: `<runner_id>.pub`. This is the production enrolment path: an operator
    #: puts the key there and nothing else is needed or accepted.
    trusted_keys_dir: str = field(
        default_factory=lambda: os.environ.get("EDGE_TRUSTED_KEYS_DIR", "trusted_runners")
    )
    #: A shared bootstrap token that lets an UNKNOWN test server enrol itself.
    #: Convenient in a lab, wrong in production — so it is empty by default and
    #: the edge says so at startup when it is set.
    enroll_token: str = field(default_factory=lambda: os.environ.get("RUNNER_ENROLL_TOKEN", ""))
    #: An operator-only view of the fleet. Empty means the endpoint 404s rather
    #: than answering — default deny, because "which machines exist and when
    #: were they last seen" is internal detail on a public surface.
    admin_token: str = field(default_factory=lambda: os.environ.get("EDGE_ADMIN_TOKEN", ""))

    # ── timings ──────────────────────────────────────────────────────────────
    #: How often a test server must check in. Four of these fit inside one
    #: lease, so a live server renews well before it could be declared dead.
    heartbeat_interval: float = field(default_factory=lambda: _num("RUNNER_HEARTBEAT_INTERVAL", 30))
    offline_after: float = field(default_factory=lambda: _num("RUNNER_OFFLINE_AFTER", 90))
    #: How long the edge holds a claim request open with nothing to give. Kept
    #: under the 30s that most proxies use as an idle timeout — a long-poll
    #: that outlives the proxy is a 504 the test server reads as an outage.
    poll_timeout: float = field(default_factory=lambda: _num("EDGE_POLL_TIMEOUT", 25))
    lease_seconds: float = field(default_factory=lambda: _num("JOB_LEASE_SECONDS", 120))
    max_attempts: int = field(default_factory=lambda: int(_num("JOB_MAX_ATTEMPTS", 2)))

    @property
    def verify_signatures(self) -> bool:
        return bool(self.signing_secret)


def load() -> EdgeConfig:
    return EdgeConfig()

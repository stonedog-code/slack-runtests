"""Configuration, read from the environment in exactly one place."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .slack import DEFAULT_CHANNEL


def _csv(name: str, default: str = "") -> frozenset[str]:
    raw = os.environ.get(name, default)
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


@dataclass(slots=True)
class Config:
    #: "local" (V1 — run pytest here) or "github" (V2 — dispatch a workflow).
    mode: str = field(default_factory=lambda: os.environ.get("RUNTESTS_MODE", "local"))

    signing_secret: str = field(
        default_factory=lambda: os.environ.get("SLACK_SIGNING_SECRET", "")
    )
    default_channel: str = field(
        default_factory=lambda: os.environ.get("SLACK_DEFAULT_CHANNEL", DEFAULT_CHANNEL)
    )

    # ── authorisation allowlists ─────────────────────────────────────────────
    # All three are separate checks and all three are needed. The signature
    # proves the request came from Slack; only `team_id` proves it came from
    # YOUR Slack; and workspace membership is not an entitlement, so channel and
    # user are checked as well. On any real team the workspace includes guests,
    # contractors and Slack Connect users from a customer.
    allowed_team: str = field(default_factory=lambda: os.environ.get("SLACK_TEAM_ID", ""))
    allowed_channels: frozenset[str] = field(
        default_factory=lambda: _csv("RUNTESTS_CHANNELS")
    )
    allowed_users: frozenset[str] = field(default_factory=lambda: _csv("RUNTESTS_USERS"))

    # ── V1: local execution ──────────────────────────────────────────────────
    suite_root: str = field(
        default_factory=lambda: os.environ.get("RUNTESTS_SUITE_ROOT", "tests/sample")
    )

    # ── V2: GitHub Actions dispatch ──────────────────────────────────────────
    github_repo: str = field(default_factory=lambda: os.environ.get("GITHUB_REPO", ""))
    github_workflow: str = field(
        default_factory=lambda: os.environ.get("GITHUB_WORKFLOW_FILE", "runtests.yml")
    )
    github_ref: str = field(default_factory=lambda: os.environ.get("GITHUB_REF_NAME", "main"))
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))

    #: Development affordance, and the one place this prototype knowingly differs
    #: from the page it implements. With no signing secret configured, signature
    #: verification is SKIPPED and every request is logged as unverified. That is
    #: how `test.sh` works out of the box. It is refused whenever a secret IS
    #: set, so the insecure path cannot survive into a configured deployment —
    #: but a real service should not have this at all.
    @property
    def verify_signatures(self) -> bool:
        return bool(self.signing_secret)


def load() -> Config:
    return Config()

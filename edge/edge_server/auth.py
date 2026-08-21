"""Who is allowed through the test-server door.

The Slack door has a signature and a five-minute window; this door needs its
own, because nothing about a request from an internal machine is signed by
Slack. It gets Ed25519 per test server (see `slack_runtests.identity`) plus one
of two enrolment paths:

    PRE-AUTHORISED  an operator drops <runner_id>.pub into the trusted keys
                    directory. Nothing else is accepted. This is production.

    BOOTSTRAP TOKEN a test server that presents the shared enrolment token may
                    register its own key, once. Convenient for a lab and for
                    the docker harness; wrong for production, so the edge logs
                    a warning for as long as it is enabled.

After enrolment the token is irrelevant — every later request is authenticated
by the key alone, so a leaked token cannot be used to impersonate a test server
that already exists (the key on file will not match).
"""

from __future__ import annotations

import logging
from pathlib import Path

from slack_runtests import identity

log = logging.getLogger(__name__)


def preauthorised_key(trusted_dir: str, runner_id: str) -> str | None:
    """Read `<trusted_dir>/<runner_id>.pub`, or None.

    `runner_id` reaches a filesystem path here, so it is checked rather than
    trusted: anything but a plain name is refused outright. A runner_id of
    `../../etc/passwd` would otherwise be a file-read primitive on the public
    edge, which is the same class of mistake the Slack parser's allowlist
    exists to prevent.
    """
    if not valid_runner_id(runner_id):
        return None
    path = Path(trusted_dir) / f"{runner_id}.pub"
    try:
        return path.read_text().strip()
    except OSError:
        return None


def valid_runner_id(runner_id: str) -> bool:
    """Names only: letters, digits, dash, underscore, 1–64 characters."""
    if not runner_id or len(runner_id) > 64:
        return False
    return all(c.isalnum() or c in "-_" for c in runner_id)


def verify_signed(
    *,
    public_key_b64: str,
    method: str,
    path: str,
    headers,
    body: bytes,
) -> bool:
    return identity.verify(
        public_key_b64,
        method,
        path,
        headers.get(identity.HEADER_TIMESTAMP, ""),
        headers.get(identity.HEADER_SIGNATURE, ""),
        body,
    )

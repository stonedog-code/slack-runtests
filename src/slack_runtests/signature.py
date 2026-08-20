"""Verify that a request really came from Slack.

Two checks, both required, and the order of operations matters more than the
cryptography does.
"""

from __future__ import annotations

import hashlib
import hmac
import time

#: Beyond this age a request is refused even with a valid signature. Without it
#: a captured request is replayable forever — an HMAC does not expire on its own.
MAX_AGE_SECONDS = 60 * 5


def is_valid(
    body: bytes,
    timestamp: str,
    signature: str,
    signing_secret: str,
    now: float | None = None,
) -> bool:
    """Check Slack's v0 signature over the RAW request body.

    THE RAW BYTES MATTER. The signature is computed over the exact bytes Slack
    sent, so it must be verified before any form parsing — re-encoding the
    parsed form and signing that produces a mismatch for reasons that are
    invisible in a debugger, and it is worth an afternoon of anyone's time.

    `hmac.compare_digest` rather than `==`: string comparison short-circuits on
    the first differing byte, which leaks the correct prefix through timing.
    """
    if not signing_secret or not timestamp or not signature:
        return False

    now = time.time() if now is None else now
    try:
        age = abs(now - int(timestamp))
    except (TypeError, ValueError):
        return False
    if age > MAX_AGE_SECONDS:
        return False

    base = b"v0:" + timestamp.encode() + b":" + body
    expected = (
        "v0="
        + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


def sign(body: bytes, timestamp: str, signing_secret: str) -> str:
    """Produce a signature. Used by `test.sh` to forge a legitimate request.

    Shipping the signer alongside the verifier is what makes the endpoint
    testable without Slack. It is the same function the real sender runs, so a
    test that passes here is exercising the real code path rather than a
    bypass — there is deliberately no "skip auth in dev" flag, because that flag
    is the one that eventually ships.
    """
    base = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()

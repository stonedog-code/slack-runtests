"""Ed25519 identity and request signing — the trust between edge and test server.

WHY A KEYPAIR AND NOT A SHARED SECRET

The Slack side of this system already has an HMAC, and an HMAC is the right tool
there because Slack and the edge are two halves of one relationship with one
secret. Between the edge and N test servers it is the wrong tool: a shared
secret means every test server can impersonate every other one, and rotating it
means touching every host at once. With a keypair per test server the private
half never leaves the machine that generated it, the edge stores only public
keys, and revoking one host is deleting one file.

BOTH DIRECTIONS ARE SIGNED, AND THE SECOND DIRECTION IS THE ONE PEOPLE FORGET.

    test server -> edge   proves *which* test server is asking for work
    edge -> test server   proves the job actually came from the edge

Without the second, anything that can answer a test server's poll can hand it a
job — and since the test server is the thing that talks to Slack, a forged job
is a forged Slack message posted from inside your own network.

WHAT IS SIGNED

    v1\\n{METHOD}\\n{path}\\n{timestamp}\\n{sha256-hex of the body}

The method and path are in there deliberately. Signing only the body lets a
captured, valid signature for `POST /runner/heartbeat` be replayed against
`POST /runner/jobs/{id}/result` — same bytes, different meaning. The timestamp
gives the replay window; an Ed25519 signature, like an HMAC, does not expire on
its own.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

#: Same window Slack uses, and for the same reason. Long enough to absorb clock
#: skew between two hosts, short enough that a captured request is not a
#: permanent credential.
MAX_AGE_SECONDS = 60 * 5

#: Headers a test server sends on every request to the edge.
HEADER_RUNNER_ID = "X-Runner-Id"
HEADER_TIMESTAMP = "X-Runner-Timestamp"
HEADER_SIGNATURE = "X-Runner-Signature"

#: Headers the edge sends back, so the test server can verify the reply.
HEADER_EDGE_TIMESTAMP = "X-Edge-Timestamp"
HEADER_EDGE_SIGNATURE = "X-Edge-Signature"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    # Never let a malformed key or signature raise out of a verifier: a
    # verifier that can throw is a verifier that turns a bad request into a
    # 500, and a 500 is a far more interesting answer to an attacker than a
    # flat "no".
    try:
        return base64.b64decode(text.strip(), validate=True)
    except Exception:
        return b""


def generate() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def public_b64(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    pub = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return _b64(
        pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )


def fingerprint(public_key_b64: str) -> str:
    """A short, stable name for a key, for logs and enrolment confirmation.

    Comparing two 44-character base64 blobs by eye is how the wrong key gets
    enrolled. Sixteen hex characters is enough to notice a mismatch.
    """
    return hashlib.sha256(_unb64(public_key_b64)).hexdigest()[:16]


def load_or_create(path: str | Path) -> Ed25519PrivateKey:
    """Read the private key at `path`, generating it on first run.

    Written 0600 and re-checked on every load. A private key that is
    world-readable on a shared box is not a private key, and the failure is
    silent — everything keeps working, which is exactly why it must be an error
    rather than a warning.
    """
    key_path = Path(path)
    if key_path.exists():
        mode = stat.S_IMODE(key_path.stat().st_mode)
        if mode & 0o077:
            raise PermissionError(
                f"{key_path} is mode {mode:o}; a private key must not be readable "
                f"by group or other. Fix with: chmod 600 {key_path}"
            )
        return serialization.load_pem_private_key(key_path.read_bytes(), password=None)  # type: ignore[return-value]

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # Create with the right mode from the start rather than chmod-ing after —
    # otherwise there is a window where the key exists and is readable.
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(pem)
    return key


def canonical(method: str, path: str, timestamp: str, body: bytes) -> bytes:
    """The exact bytes both sides sign. Method and path included on purpose."""
    digest = hashlib.sha256(body).hexdigest()
    return "\n".join(["v1", method.upper(), path, str(timestamp), digest]).encode()


def sign(key: Ed25519PrivateKey, method: str, path: str, timestamp: str, body: bytes) -> str:
    return _b64(key.sign(canonical(method, path, timestamp, body)))


def verify(
    public_key_b64: str,
    method: str,
    path: str,
    timestamp: str,
    signature_b64: str,
    body: bytes,
    now: float | None = None,
    max_age: int = MAX_AGE_SECONDS,
) -> bool:
    """True only if the signature is valid AND the request is recent.

    Returns False for every failure mode — bad base64, wrong length key,
    unparseable timestamp, stale request, bad signature. The caller gets one
    answer and learns nothing about which check failed, which is the point.
    """
    if not (public_key_b64 and timestamp and signature_b64):
        return False

    now = time.time() if now is None else now
    try:
        age = abs(now - int(timestamp))
    except (TypeError, ValueError):
        return False
    if age > max_age:
        return False

    raw_key = _unb64(public_key_b64)
    raw_sig = _unb64(signature_b64)
    if len(raw_key) != 32 or len(raw_sig) != 64:
        return False

    try:
        Ed25519PublicKey.from_public_bytes(raw_key).verify(
            raw_sig, canonical(method, path, timestamp, body)
        )
    except (InvalidSignature, ValueError):
        return False
    return True


def sign_reply(key: Ed25519PrivateKey, timestamp: str, body: bytes) -> str:
    """Sign a response body. Path is not included — a reply has no route."""
    digest = hashlib.sha256(body).hexdigest()
    payload = "\n".join(["v1-reply", str(timestamp), digest]).encode()
    return _b64(key.sign(payload))


def verify_reply(
    public_key_b64: str,
    timestamp: str,
    signature_b64: str,
    body: bytes,
    now: float | None = None,
    max_age: int = MAX_AGE_SECONDS,
) -> bool:
    if not (public_key_b64 and timestamp and signature_b64):
        return False
    now = time.time() if now is None else now
    try:
        if abs(now - int(timestamp)) > max_age:
            return False
    except (TypeError, ValueError):
        return False

    raw_key = _unb64(public_key_b64)
    raw_sig = _unb64(signature_b64)
    if len(raw_key) != 32 or len(raw_sig) != 64:
        return False

    digest = hashlib.sha256(body).hexdigest()
    payload = "\n".join(["v1-reply", str(timestamp), digest]).encode()
    try:
        Ed25519PublicKey.from_public_bytes(raw_key).verify(raw_sig, payload)
    except (InvalidSignature, ValueError):
        return False
    return True

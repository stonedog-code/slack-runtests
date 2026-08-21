"""The Ed25519 channel between edge and test servers.

Each test here corresponds to something an attacker would try. A signature
scheme that is only ever tested on the happy path is a scheme nobody has
checked, because the happy path passes with `return True` in the verifier.
"""

from __future__ import annotations

import os
import stat
import time

import pytest

from slack_runtests import identity

pytestmark = pytest.mark.unit


@pytest.fixture
def key():
    return identity.generate()


def test_a_correctly_signed_request_verifies(key) -> None:
    body = b'{"runner_id":"runner-1"}'
    ts = str(int(time.time()))
    sig = identity.sign(key, "POST", "/runner/heartbeat", ts, body)

    assert identity.verify(identity.public_b64(key), "POST", "/runner/heartbeat", ts, sig, body)


def test_a_different_key_does_not_verify(key) -> None:
    """The whole point of per-server keys: one cannot speak for another."""
    other = identity.generate()
    ts = str(int(time.time()))
    sig = identity.sign(key, "POST", "/runner/heartbeat", ts, b"{}")

    assert not identity.verify(identity.public_b64(other), "POST", "/runner/heartbeat", ts, sig, b"{}")


def test_a_tampered_body_does_not_verify(key) -> None:
    ts = str(int(time.time()))
    sig = identity.sign(key, "POST", "/runner/jobs/j1/result", ts, b'{"exit_code":1}')

    assert not identity.verify(
        identity.public_b64(key), "POST", "/runner/jobs/j1/result", ts, sig, b'{"exit_code":0}'
    )


def test_a_signature_cannot_be_replayed_against_a_different_path(key) -> None:
    """Why method and path are inside the signed string.

    Sign only the body and a captured heartbeat — same empty JSON, valid
    signature, recent timestamp — replays against the result endpoint. Binding
    the route makes each signature good for exactly one call.
    """
    ts = str(int(time.time()))
    sig = identity.sign(key, "POST", "/runner/heartbeat", ts, b"{}")

    assert not identity.verify(
        identity.public_b64(key), "POST", "/runner/jobs/j1/result", ts, sig, b"{}"
    )


def test_a_signature_cannot_be_replayed_against_a_different_method(key) -> None:
    ts = str(int(time.time()))
    sig = identity.sign(key, "POST", "/runner/heartbeat", ts, b"{}")

    assert not identity.verify(
        identity.public_b64(key), "GET", "/runner/heartbeat", ts, sig, b"{}"
    )


def test_a_stale_request_is_refused_even_with_a_valid_signature(key) -> None:
    """An Ed25519 signature does not expire on its own; the window is what expires."""
    old = str(int(time.time()) - identity.MAX_AGE_SECONDS - 30)
    sig = identity.sign(key, "POST", "/runner/heartbeat", old, b"{}")

    assert not identity.verify(identity.public_b64(key), "POST", "/runner/heartbeat", old, sig, b"{}")


def test_a_future_request_is_refused_too(key) -> None:
    """Clock skew is symmetric; so is the check. `abs()`, not `>`."""
    future = str(int(time.time()) + identity.MAX_AGE_SECONDS + 30)
    sig = identity.sign(key, "POST", "/runner/heartbeat", future, b"{}")

    assert not identity.verify(identity.public_b64(key), "POST", "/runner/heartbeat", future, sig, b"{}")


@pytest.mark.parametrize(
    "public_key, timestamp, signature",
    [
        ("", "1", "sig"),
        ("not base64!!", "1", "sig"),
        ("AAAA", "1", "sig"),            # valid base64, wrong length
        ("", "", ""),
        ("A" * 44, "not-a-number", "A" * 88),
    ],
)
def test_garbage_returns_false_rather_than_raising(public_key, timestamp, signature) -> None:
    """A verifier that can raise turns a bad request into a 500.

    A 500 is a much more interesting answer to an attacker than a flat refusal,
    and it is also how a malformed key file takes the edge down rather than
    rejecting one test server.
    """
    assert identity.verify(public_key, "POST", "/x", timestamp, signature, b"") is False


def test_replies_are_signed_and_verified(key) -> None:
    ts = str(int(time.time()))
    body = b'{"job_id":"abc"}'
    sig = identity.sign_reply(key, ts, body)

    assert identity.verify_reply(identity.public_b64(key), ts, sig, body)
    assert not identity.verify_reply(identity.public_b64(identity.generate()), ts, sig, body)
    assert not identity.verify_reply(identity.public_b64(key), ts, sig, b'{"job_id":"other"}')


def test_a_new_private_key_is_written_unreadable_to_anyone_else(tmp_path) -> None:
    path = tmp_path / "nested" / "runner_ed25519.pem"
    identity.load_or_create(path)

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode & 0o077 == 0, f"key is mode {mode:o}"


def test_the_same_key_is_returned_on_reload(tmp_path) -> None:
    """A restart must not change this server's identity.

    The runner id plus its key is what job ownership is checked against, so a
    server that comes back as a different principal has silently abandoned
    whatever it was holding.
    """
    path = tmp_path / "runner_ed25519.pem"
    first = identity.public_b64(identity.load_or_create(path))
    second = identity.public_b64(identity.load_or_create(path))

    assert first == second


def test_a_widened_key_file_is_refused(tmp_path) -> None:
    """Loud rather than silent. Everything still *works* with a 0644 key."""
    path = tmp_path / "runner_ed25519.pem"
    identity.load_or_create(path)
    os.chmod(path, 0o644)

    with pytest.raises(PermissionError, match="chmod 600"):
        identity.load_or_create(path)


def test_fingerprints_differ_between_keys() -> None:
    a = identity.fingerprint(identity.public_b64(identity.generate()))
    b = identity.fingerprint(identity.public_b64(identity.generate()))

    assert a != b and len(a) == 16

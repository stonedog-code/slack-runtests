import time

import pytest

from slack_runtests.signature import MAX_AGE_SECONDS, is_valid, sign

pytestmark = pytest.mark.unit

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
BODY = b"token=xyz&team_id=T1&text=-p+webapp"


def test_a_signature_this_module_produced_verifies() -> None:
    ts = str(int(time.time()))
    assert is_valid(BODY, ts, sign(BODY, ts, SECRET), SECRET)


def test_a_tampered_body_fails() -> None:
    ts = str(int(time.time()))
    sig = sign(BODY, ts, SECRET)
    assert not is_valid(BODY + b"&evil=1", ts, sig, SECRET)


def test_the_wrong_secret_fails() -> None:
    ts = str(int(time.time()))
    assert not is_valid(BODY, ts, sign(BODY, ts, SECRET), "different-secret")


def test_an_old_timestamp_is_refused_even_with_a_valid_signature() -> None:
    # An HMAC does not expire. Without the age check a captured request is
    # replayable forever.
    old = str(int(time.time()) - MAX_AGE_SECONDS - 1)
    assert not is_valid(BODY, old, sign(BODY, old, SECRET), SECRET)


def test_a_future_timestamp_is_refused_too() -> None:
    # abs() on the age, so clock skew in either direction is bounded.
    future = str(int(time.time()) + MAX_AGE_SECONDS + 1)
    assert not is_valid(BODY, future, sign(BODY, future, SECRET), SECRET)


@pytest.mark.parametrize("timestamp", ["", "not-a-number", "12.5"])
def test_a_malformed_timestamp_is_refused_and_does_not_raise(timestamp: str) -> None:
    assert not is_valid(BODY, timestamp, "v0=deadbeef", SECRET)


def test_missing_pieces_are_refused() -> None:
    ts = str(int(time.time()))
    assert not is_valid(BODY, ts, "", SECRET)
    assert not is_valid(BODY, "", "v0=x", SECRET)
    assert not is_valid(BODY, ts, "v0=x", "")

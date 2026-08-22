"""The library tests import. Its console fallback is the behaviour under test."""

import io
import logging

import pytest

from slack_runtests.slack import (
    DEFAULT_CHANNEL,
    SlackNotifier,
    announce_configuration,
    configured,
    notify,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test here runs credential-less. That is the interesting path, and
    it also guarantees the suite can never post to a real workspace."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)


def test_default_channel_is_testing() -> None:
    assert DEFAULT_CHANNEL == "#testing"
    assert SlackNotifier().channel == "#testing"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (None, "#testing"),
        ("", "#testing"),
        ("   ", "#testing"),
        ("releases", "#releases"),
        ("#releases", "#releases"),
        ("C0123ABC", "C0123ABC"),  # a raw channel id passes through untouched
    ],
)
def test_channel_normalisation(given, expected) -> None:
    assert SlackNotifier(channel=given).channel == expected


def test_without_a_token_it_is_disabled_and_prints_the_message_and_channel() -> None:
    stream = io.StringIO()
    SlackNotifier(channel="#ci", stream=stream).post("hello world")
    output = stream.getvalue()
    assert "dry-run" in output
    assert "#ci" in output          # WHICH channel
    assert "hello world" in output  # WHAT would have been sent


def test_it_does_not_raise_without_credentials() -> None:
    # The whole point: a test suite can call this unconditionally. Raising would
    # make every reporting test fail on a laptop, so people would stop calling it.
    n = SlackNotifier(stream=io.StringIO())
    assert n.enabled is False
    n.start("x")
    n.progress(passed=1, failed=0, total=2)
    n.finish(passed=2, failed=0)


def test_a_token_flips_it_to_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-not-real")
    assert SlackNotifier().enabled is True


def test_the_token_is_read_at_call_time_not_import_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Import-time reads surprise anyone who sets the variable afterwards, which
    # is exactly what monkeypatch.setenv in a fixture does.
    assert SlackNotifier().enabled is False
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-not-real")
    assert SlackNotifier().enabled is True


def test_update_is_rate_limited_but_force_bypasses_it() -> None:
    # chat.update is limited per method per app, so an unthrottled 500-test suite
    # would break every other thing the bot posts, not just this run.
    n = SlackNotifier(stream=io.StringIO())
    n.post("start")
    assert n.update("first") is not None    # first edit goes through
    assert n.update("second") is None       # throttled
    assert n.update("third", force=True) is not None


def test_update_before_post_falls_back_to_posting() -> None:
    stream = io.StringIO()
    n = SlackNotifier(stream=stream)
    assert n.update("no prior message") is not None
    assert "no prior message" in stream.getvalue()


def test_finish_summarises_and_never_carries_output() -> None:
    stream = io.StringIO()
    n = SlackNotifier(stream=stream)
    n.post("start")
    n.finish(passed=48, failed=2, skipped=1, duration=91.4,
             failed_ids=["test_a", "test_b"])
    text = stream.getvalue()
    assert "48 passed" in text and "2 failed" in text and "1 skipped" in text
    assert "test_a" in text


def test_finish_truncates_a_long_failure_list() -> None:
    stream = io.StringIO()
    n = SlackNotifier(stream=stream)
    n.post("start")
    n.finish(passed=0, failed=9, failed_ids=[f"test_{i}" for i in range(9)])
    assert "+4 more" in stream.getvalue()


def test_notify_returns_where_it_would_have_gone() -> None:
    sent = notify("one-shot")
    assert sent.channel == "#testing"
    assert sent.dry_run is True


def test_notify_builds_a_fresh_notifier_each_call() -> None:
    # A module-level singleton would cache the channel from whichever test ran
    # first — precisely the coupling that makes a suite order-dependent.
    assert notify("a", channel="#one").channel == "#one"
    assert notify("b").channel == "#testing"


# ── the startup announcement ─────────────────────────────────────────────────
#
# Every message already prints itself when there is no token (above). What these
# cover is the SECOND half of the same promise: saying so at startup, before any
# message exists. An operator who starts the service needs to know it is inert
# then, not hours later when the first run silently reports nowhere.


def test_startup_warns_that_slack_is_not_configured(caplog: pytest.LogCaptureFixture) -> None:
    log = logging.getLogger("test-startup")
    with caplog.at_level(logging.INFO):
        assert announce_configuration(log, "#ci") is False

    message = caplog.text
    assert "SLACK_BOT_TOKEN" in message        # WHICH knob is unset
    assert "NOT configured" in message
    assert "#ci" in message                    # WHERE it would have gone
    assert "[slack:dry-run]" in message        # WHAT to search the console for
    assert caplog.records[-1].levelno == logging.WARNING


def test_startup_says_so_when_a_token_is_present(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The other direction. A warning that is emitted unconditionally tells an
    # operator nothing, because it is present whether or not anything is wrong.
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-real-looking-token")
    log = logging.getLogger("test-startup")
    with caplog.at_level(logging.INFO):
        assert announce_configuration(log, "#ci") is True

    assert "SLACK_BOT_TOKEN unset" not in caplog.text
    assert caplog.records[-1].levelno == logging.INFO
    assert "#ci" in caplog.text


def test_startup_does_not_invent_a_channel_it_does_not_know(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A test server learns its channel from each job, so it has none at startup.
    # Printing the #testing default there would be a confident falsehood in the
    # one line an operator is trusting to tell them what is going on.
    log = logging.getLogger("test-startup")
    with caplog.at_level(logging.INFO):
        announce_configuration(log)

    assert "#testing" not in caplog.text
    assert "the channel each job names" in caplog.text


def test_configured_tracks_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    assert configured() is False
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-abc")
    assert configured() is True
    # Read at call time, never cached at import — same guarantee as _token().
    monkeypatch.delenv("SLACK_BOT_TOKEN")
    assert configured() is False

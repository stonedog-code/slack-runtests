"""The three requests that define the Slack door, against a real server.

    1. A valid test origin, valid structure     -> accepted
    2. An invalid origin                        -> rejected
    3. A valid origin, invalid structure        -> refused

Together they pin the two halves of the boundary. The first proves the door
opens for the traffic it is for — a check that matters more than it looks,
because a door that never opens also passes tests 2 and 3. The second proves it
is shut to anything that cannot prove it is Slack. The third proves that proving
you are Slack is *not enough*: what you typed still has to be on the allowlist.

WHAT "ORIGIN" MEANS HERE, AND WHAT IT DOES NOT

Test 2 sends a request signed with the wrong secret — a sender that cannot prove
it is Slack at all. There is a second, different notion of a bad origin: a
correctly-signed request from *another Slack workspace*, which the edge refuses
on `team_id`. That is a distinct control with a distinct failure shape (an
ephemeral refusal, not a 401) and it has its own unit test; it is called out
here so nobody reads these three as covering it.
"""

from __future__ import annotations

import pytest
from harness import post, slack_body

pytestmark = pytest.mark.integration


# ── 1 ────────────────────────────────────────────────────────────────────────

def test_valid_origin_and_valid_structure_is_accepted(edge) -> None:
    """A genuine request from the allowlisted workspace, channel and user."""
    response = post(edge, slack_body("-p webapp -s staging"))

    assert response.status_code == 200, response.text
    payload = response.json()
    # Ephemeral: an acknowledgement belongs to the person who typed the command,
    # not to everyone in the channel. The *result* is the opposite, and a test
    # server posts that.
    assert payload["response_type"] == "ephemeral"
    assert "Queued" in payload["text"]
    assert "webapp" in payload["text"] and "staging" in payload["text"]


# ── 2 ────────────────────────────────────────────────────────────────────────

def test_invalid_origin_is_rejected(edge) -> None:
    """Correctly formed, correctly addressed — and signed by the wrong party.

    401 rather than a friendly message, and deliberately so: a sender that
    cannot prove it is Slack is not a person to help, and telling it why it
    failed is telling it how to succeed.
    """
    response = post(edge, slack_body("-p webapp -s staging"), secret="not-the-real-signing-secret")

    assert response.status_code == 401
    body = response.text.lower()
    # The refusal must not leak the shape of the check. Nothing about the real
    # secret, the expected signature, or the allowlists.
    assert "not-the-real-signing-secret" not in body
    assert "queued" not in body


# ── 3 ────────────────────────────────────────────────────────────────────────

def test_valid_origin_with_invalid_structure_is_refused(edge) -> None:
    """Genuinely from Slack, and asking for something that is not allowed.

    `-p ../../etc` is a path traversal and `-s prod` is an environment that is
    deliberately absent from the allowlist. Both are refused by the parser
    before any value reaches a path or a runner.

    NOTE THE STATUS CODE. This asserts 200, not 4xx, and that is not a
    concession: Slack's contract is that a user error is a 200 with an
    ephemeral body. Returning an HTTP error would make Slack show its own
    generic "dispatch_failed" instead of the reason the command was wrong —
    which is unhelpful exactly when the user most needs help. So the assertion
    that matters is on the body.
    """
    response = post(edge, slack_body("-p ../../etc -s prod"))

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["response_type"] == "ephemeral"
    text = payload["text"]

    assert "Queued" not in text, "a refused command must not have been queued"
    assert "/runtests" in text
    assert "Try:" in text, "a refusal should show the usage hint"
    # The message names the allowed products, which is what makes it useful
    # rather than merely correct. It does echo the rejected value back, and
    # that is fine HERE and only here: the reply is ephemeral, so the only
    # person who sees their own input reflected is the person who typed it.
    # The same string in a channel message would be a different question.
    assert "webapp" in text and "billing" in text

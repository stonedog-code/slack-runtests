"""Fixed identities and request-building for the integration tier.

Separate from `conftest.py` so the test module can import it by name. A test
file cannot do a relative import from a conftest — there is no package — and
`import conftest` works but reads like a mistake.
"""

from __future__ import annotations

import time
import urllib.parse
import uuid
from dataclasses import dataclass

import httpx

from slack_runtests.signature import sign

#: The "valid test Slack origin": real-looking identifiers that belong to
#: nobody. The edge is started with exactly these on its allowlists, so a
#: request carrying them is legitimate *because it was configured to be* —
#: which is the same mechanism a real workspace uses, not a test-only bypass.
TEST_SIGNING_SECRET = "integration-test-signing-secret"
TEST_TEAM_ID = "T_INTEGRATION"
TEST_CHANNEL_ID = "C_INTEGRATION"
TEST_CHANNEL_NAME = "testing"
TEST_USER_ID = "U_INTEGRATION"


@dataclass
class EdgeUnderTest:
    url: str
    signing_secret: str
    #: True when the fixture spawned the process and is responsible for it.
    managed: bool


def slack_body(text: str, **overrides: str) -> bytes:
    """A payload shaped exactly like Slack's, with a fresh trigger per call.

    The trigger id must be new every time: the edge keys idempotency on it, so
    a fixed value would make the second call in a session collide with the
    first and get "already queued" instead of "queued" — a failure that looks
    like a broken assertion and is really a stale fixture.
    """
    fields = {
        "token": "gIkuvaNzQIHg97ATvDxqgjtO",
        "team_id": TEST_TEAM_ID,
        "team_domain": "example",
        "channel_id": TEST_CHANNEL_ID,
        "channel_name": TEST_CHANNEL_NAME,
        "user_id": TEST_USER_ID,
        "user_name": "qa.bot",
        "command": "/runtests",
        "text": text,
        "api_app_id": "A0123456789",
        "response_url": "https://hooks.slack.com/commands/1234/5678",
        "trigger_id": f"integration-{uuid.uuid4().hex}",
    }
    fields.update(overrides)
    return urllib.parse.urlencode(fields).encode()


def post(edge: EdgeUnderTest, body: bytes, *, secret: str | None = None,
         timestamp: str | None = None) -> httpx.Response:
    """POST a signed slash command over a real socket.

    `secret` defaults to the one the server was started with — pass a different
    one to forge a request that is well-formed but not from Slack.
    """
    ts = timestamp or str(int(time.time()))
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": sign(body, ts, secret if secret is not None else edge.signing_secret),
    }
    return httpx.post(f"{edge.url}/slack/commands", content=body, headers=headers, timeout=15)

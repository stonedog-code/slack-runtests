"""What the edge HANDS ON, not just what it answers.

`test_slack_door.py` proves the edge replies correctly to a signed slash
command. That is half of the boundary. The other half is the payload it parks
for a test server — the one thing in this system that crosses from a public host
onto a machine inside your network — and no test above the unit tier had ever
looked at it. A door that answers "queued" and queues the wrong thing passes
every test in that file.

So these tests walk the whole V3 dispatch path over real sockets:

    a signed Slack command  ->  the edge  ->  the queue
                                      |
    an enrolled test server  ->  claim  ->  the dispatch payload

and assert the payload matches the command that produced it, field by field,
with the job id tying the two together.

WHY THE JOB ID IS COMPARED RATHER THAN ASSUMED
The `edge` fixture is session-scoped, so other tests have queued jobs against
it. Claiming "a job" and checking its fields would pass just as happily on
somebody else's job. The edge's ephemeral reply names the id it created, so the
claim is checked against THAT id — a mismatch fails loudly instead of asserting
the right shape about the wrong row.

WHY THE TEST SERVER IS LABELLED
`-s dev` with a runner labelled `dev` also keeps this test off the staging jobs
the Slack-door tests leave queued, and exercises the routing that decides which
machine takes which environment.
"""

from __future__ import annotations

import re

import pytest
from harness import (
    TEST_CHANNEL_NAME,
    TEST_USER_ID,
    RunnerIdentity,
    edge_public_key,
    post,
    reply_is_signed_by_edge,
    slack_body,
)

from slack_runtests import identity

pytestmark = pytest.mark.integration

#: `⏳ Queued `billing` on `dev` (`a1b2c3d4e5f6`) — 1 test server(s) available.`
JOB_ID = re.compile(r"\(`([0-9a-f]{12})`\)")


@pytest.fixture(scope="module")
def runner(edge) -> RunnerIdentity:
    """A real test server: its own key, pre-authorised, enrolled over HTTP."""
    if edge.trusted_keys_dir is None:
        pytest.skip(
            "RUNTESTS_EDGE_URL points at a server whose key directory this suite "
            "cannot know, so a test server cannot be enrolled against it. CI never "
            "sets that variable, so this never skips in the gate."
        )

    who = RunnerIdentity.create("integration-runner-1")
    who.preauthorise(edge.trusted_keys_dir)

    response = who.post(edge, "/runner/enroll", {
        "runner_id": who.runner_id,
        "public_key": who.public_key,
        "labels": ["dev"],
    })
    assert response.status_code == 200, response.text
    # Mutual authentication, verified rather than assumed: the reply the test
    # server acts on must be provably from the edge.
    assert reply_is_signed_by_edge(response, edge_public_key(edge)), \
        "the edge did not sign its enrolment reply"
    return who


def queue_a_job(edge, command: str) -> str:
    """Send a signed slash command and return the job id the edge created."""
    response = post(edge, slack_body(command))
    assert response.status_code == 200, response.text
    text = response.json()["text"]
    assert "Queued" in text, text
    match = JOB_ID.search(text)
    assert match, f"the reply did not name a job id: {text!r}"
    return match.group(1)


# ── 1 ────────────────────────────────────────────────────────────────────────

def test_the_dispatch_payload_matches_the_command_that_produced_it(edge, runner) -> None:
    """Every field a test server acts on, traced back to the typed command."""
    job_id = queue_a_job(edge, '-p billing -s dev -k "smoke and not slow"')

    response = runner.post(edge, "/runner/jobs/claim")
    assert response.status_code == 200, response.text
    payload = response.json()

    # Field by field, and as a whole. The whole-dict comparison is what catches
    # a NEW field appearing — an added key is exactly how a secret would first
    # arrive on this payload, and a per-field check would never notice.
    assert payload == {
        "job_id": job_id,
        "product": "billing",
        "server": "dev",
        # One argument, not four. `-k "smoke and not slow"` survives shlex at
        # the door, the form encoding, SQLite and JSON with its spaces intact.
        "select": "smoke and not slow",
        "marker": "",
        "slack_channel": f"#{TEST_CHANNEL_NAME}",
        "slack_user": TEST_USER_ID,
    }


# ── 2 ────────────────────────────────────────────────────────────────────────

def test_the_dispatch_payload_carries_nothing_secret(edge, runner) -> None:
    """The job payload is minimal ON PURPOSE, so assert the absences.

    A job payload is the one thing that crosses from the public edge onto an
    internal machine. The less it carries the less a forged one could do — and
    "the less it carries" is a property that only stays true if something checks
    it. The comparison in test 1 catches a new field; this catches a secret
    smuggled into an existing one.
    """
    job_id = queue_a_job(edge, "-p catalog -s dev")

    response = runner.post(edge, "/runner/jobs/claim")
    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == job_id

    raw = response.text
    for secret in (
        "integration-test-signing-secret",  # the Slack signing secret
        "T_INTEGRATION",                    # the workspace id
        "xoxb-",                            # any Slack bot token
        "BEGIN PRIVATE KEY",                # any key material
        "gIkuvaNzQIHg97ATvDxqgjtO",         # Slack's legacy verification token
    ):
        assert secret not in raw, f"the dispatch payload leaked {secret!r}"

    assert reply_is_signed_by_edge(response, edge_public_key(edge)), \
        "the edge did not sign the job it dispatched"


# ── 3 ────────────────────────────────────────────────────────────────────────

def test_a_job_is_dispatched_once_and_then_the_queue_is_empty(edge, runner) -> None:
    """At-most-once while a lease holds, and 204 rather than a repeat.

    Tests 1 and 2 have drained every `dev` job. A further claim must not hand
    one of them out a second time: two test servers running the same suite
    against the same environment is the failure the lease exists to bound.
    """
    response = runner.post(edge, "/runner/jobs/claim")

    assert response.status_code == 204, response.text
    assert response.content == b""


# ── 4 ────────────────────────────────────────────────────────────────────────

def test_the_runner_door_refuses_a_request_signed_by_the_wrong_key(edge, runner) -> None:
    """An enrolled id is not a credential — the key is.

    The test servers are what talk to Slack, so anything that can feed one a job
    can post a message in your channel from inside your network. This is that
    door, checked over a real socket rather than in-process.
    """
    response = runner.post(
        edge, "/runner/jobs/claim", key_override=identity.generate()
    )

    assert response.status_code == 401
    body = response.text.lower()
    # A flat refusal: an unknown runner id and a bad signature must look the
    # same, or the endpoint becomes an oracle for which test servers exist.
    assert "job_id" not in body and runner.runner_id.lower() not in body

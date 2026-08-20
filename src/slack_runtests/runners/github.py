"""V2 — dispatch a GitHub Actions workflow instead of running anything here.

WHY THIS EXISTS, AND IT IS THE MOST IMPORTANT DESIGN DECISION IN THE PROJECT:

    The public API never runs a test.

It authenticates, authorises, validates, dispatches, and answers. Everything
that touches your network happens on a self-hosted runner that POLLS GitHub, so
it needs no inbound connectivity and no port open to the internet. In V1 the
process answering the internet is the process running the tests; here they are
different machines, and the one on your network only ever calls out.

You also inherit logs, artifacts, retention, concurrency limits and an approval
gate without writing any of them.
"""

from __future__ import annotations

import logging

import httpx

from ..slack import SlackNotifier

log = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


async def dispatch(
    *,
    repo: str,
    workflow: str,
    ref: str,
    token: str,
    correlation_id: str,
    product: str,
    server: str,
    select: str | None,
    marker: str | None,
    slack_channel: str,
    slack_user: str,
) -> bool:
    """Fire a `workflow_dispatch`. Returns whether GitHub accepted it.

    Pass the channel and the user through as INPUTS. It is easy to design this
    whole chain and never notice that the reporter at the far end has no idea
    which conversation started it — the channel id is in the Slack payload and
    nowhere else.
    """
    slack = SlackNotifier(channel=slack_channel)

    if not (repo and token):
        # Dry-run parity with slack.py: say exactly what would have happened
        # rather than failing or, worse, silently doing nothing.
        log.warning("GITHUB_REPO/GITHUB_TOKEN unset — not dispatching")
        slack.post(
            "[github:dry-run] would dispatch "
            f"`{workflow}` on `{repo or '<unset repo>'}@{ref}` "
            f"for `{product}` @ `{server}` (id `{correlation_id}`)"
        )
        return False

    inputs = {
        # workflow_dispatch allows at most 10 inputs and every value must be a
        # string — an int here is a 422 that reads as a schema problem.
        "correlation_id": correlation_id,
        "product": product,
        "server": server,
        "select": select or "",
        "marker": marker or "",
        "slack_channel": slack_channel,
        "slack_user": slack_user,
    }

    url = f"{GITHUB_API}/repos/{repo}/actions/workflows/{workflow}/dispatches"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"ref": ref, "inputs": inputs},
            )
    except httpx.HTTPError as exc:
        log.warning("dispatch failed: %s", exc)
        slack.post(f"Could not reach GitHub to start the run: {exc}")
        return False

    if response.status_code != 204:
        # Do NOT echo the response body to Slack — it can contain repo names and
        # other internal detail, and the channel is a wider audience than the
        # person who typed the command.
        log.warning("dispatch rejected %s: %s", response.status_code, response.text[:400])
        slack.post(f"GitHub refused the run (HTTP {response.status_code}). Check the API logs.")
        return False

    # ── The thing this call does NOT give you ────────────────────────────────
    # `workflow_dispatch` answers 204 No Content with NO RUN ID, so the API
    # cannot tell the user where their run is.
    #
    # The tempting fix — poll GET /actions/runs until it appears — is a trap
    # twice over: that listing is eventually consistent so the first call
    # usually finds nothing, and waiting for it here breaches Slack's
    # three-second budget, which makes Slack retry, which queues a SECOND
    # identical run.
    #
    # So do not correlate from the API at all. Let the runner introduce itself:
    # it knows its own run id, and the first thing it posts carries the link.
    slack.post(f"⏳ Queued `{product}` on `{server}` — the runner will post here (`{correlation_id}`).")
    return True

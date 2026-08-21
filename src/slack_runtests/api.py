"""The public endpoint Slack posts to.

THE THREE-SECOND BUDGET SHAPES THIS ENTIRE FILE. Slack shows the user an error
and RETRIES the request if a 200 is not back within three seconds. So everything
before `background.add_task` is cheap on purpose, and the tests never run in the
handler — not in V1 either, where they run in a background task.

A retry that starts a second identical run is the failure this design is mostly
avoiding, which is also why dispatch is keyed on a stable correlation id.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

from . import gate
from .config import Config, load
from .runners import github as github_runner
from .runners import local as local_runner

log = logging.getLogger(__name__)

app = FastAPI(title="slack-runtests")

#: In-memory record of what has been dispatched, so `results` can answer and so
#: a Slack retry does not start a second run. A real deployment needs this in
#: Redis or Postgres — an in-process dict is per-worker, so two uvicorn workers
#: would each have their own and the idempotency guarantee quietly disappears.
_RUNS: dict[str, dict] = {}


def _config(request: Request) -> Config:
    """Config per request, so tests can override it on app.state."""
    return getattr(request.app.state, "config", None) or load()


def ephemeral(text: str) -> JSONResponse:
    """Reply visible only to the person who typed the command.

    Ephemeral by default is the right choice: an error, a usage hint or a
    "not authorised" belongs to the user, not to everyone in the channel.
    """
    return JSONResponse({"response_type": "ephemeral", "text": text})


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness. Deliberately reveals nothing about configuration."""
    return {"status": "ok"}


@app.post("/slack/commands")
async def commands(request: Request, background: BackgroundTasks) -> JSONResponse:
    cfg = _config(request)

    # THE RAW BODY, before any form parsing — the signature is over the bytes
    # Slack sent. Re-encoding a parsed form and signing that gives a mismatch
    # for reasons invisible in a debugger.
    body = await request.body()

    # All four checks — signature, workspace, identity, wording — live in
    # gate.py, shared with the edge server. One implementation of the boundary
    # rather than two that drift apart, which is how one gets fixed and the
    # other quietly does not.
    outcome = gate.check(
        body,
        request.headers,
        signing_secret=cfg.signing_secret,
        allowed_team=cfg.allowed_team,
        allowed_channels=cfg.allowed_channels,
        allowed_users=cfg.allowed_users,
    )
    if not outcome.ok:
        if outcome.status != 200:
            return JSONResponse({"error": outcome.message}, status_code=outcome.status)
        # A rejected command is a 200 with an ephemeral body: that is Slack's
        # contract for a user error, and answering 4xx here would make Slack
        # show its own generic failure instead of the reason.
        return ephemeral(str(outcome.message))

    form = outcome.form
    args = outcome.args
    assert args is not None  # ok=True guarantees it; this narrows for the type checker

    # 4. Idempotency. Slack retries anything slow or non-2xx (X-Slack-Retry-Num),
    #    so key on trigger_id — unique per invocation — and make a repeat a
    #    no-op. Without this, one slow morning becomes four identical runs
    #    against the same box.
    trigger = str(form.get("trigger_id") or form.get("text", ""))
    correlation_id = uuid.uuid5(uuid.NAMESPACE_URL, trigger).hex[:12]

    channel = str(form.get("channel_name") or form.get("channel_id") or cfg.default_channel)

    if args.action == "results":
        run = _RUNS.get(correlation_id) or _last_run_for(args.product)
        if not run:
            return ephemeral(f"No recorded run for `{args.product}` yet.")
        return ephemeral(
            f"Last `{run['product']}` run on `{run['server']}` — "
            f"id `{run['correlation_id']}`, started {int(time.time() - run['started'])}s ago, "
            f"mode `{run['mode']}`."
        )

    if correlation_id in _RUNS:
        return ephemeral(f"That run is already queued (`{correlation_id}`).")

    _RUNS[correlation_id] = {
        "correlation_id": correlation_id,
        "product": args.product,
        "server": args.server,
        "started": time.time(),
        "mode": cfg.mode,
    }

    # 5. Dispatch in the background and answer immediately.
    if cfg.mode == "github":
        background.add_task(
            github_runner.dispatch,
            repo=cfg.github_repo,
            workflow=cfg.github_workflow,
            ref=cfg.github_ref,
            token=cfg.github_token,
            correlation_id=correlation_id,
            product=args.product,
            server=args.server,
            select=args.select,
            marker=args.marker,
            slack_channel=channel,
            slack_user=str(form.get("user_id", "")),
        )
    else:
        background.add_task(
            local_runner.run,
            product=args.product,
            server=args.server,
            select=args.select,
            marker=args.marker,
            channel=channel,
            suite_root=cfg.suite_root,
            cwd=Path.cwd(),
        )

    return ephemeral(
        f"⏳ Queued `{args.product}` on `{args.server}` "
        f"(`{cfg.mode}`, id `{correlation_id}`) — results will post to {channel}."
    )


def _last_run_for(product: str) -> dict | None:
    matches = [r for r in _RUNS.values() if r["product"] == product]
    return max(matches, key=lambda r: r["started"]) if matches else None

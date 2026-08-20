"""V1 — run pytest directly on this machine.

This is the simplest thing that works and it is genuinely useful: one host, no
GitHub App, no self-hosted runner, no public URL beyond the API itself. It is
also the version you should not expose to a wide Slack workspace, because the
process that answers the internet is the process that runs the tests. V2 exists
to break exactly that link.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..parsing import EXPRESSION
from ..slack import SlackNotifier

log = logging.getLogger(__name__)


def build_argv(product: str, server: str, select: str | None, marker: str | None,
               suite_root: str) -> list[str]:
    """Assemble the pytest command as a LIST.

    A list is passed straight to `subprocess.run` with no shell, so a value
    containing `;` or a space is one argument and can never be reinterpreted as
    shell syntax. The parser has already allowlisted `product` and `server` and
    regex-checked the expressions; this is the second lock.
    """
    launcher = ["uv", "run", "pytest"] if shutil.which("uv") else [sys.executable, "-m", "pytest"]
    argv = [*launcher, f"{suite_root}/{product}", f"--server={server}"]
    if select:
        argv += ["-k", select]
    if marker:
        argv += ["-m", marker]
    argv += ["-q", "--junit-xml=results.xml"]
    return argv


def run(
    product: str,
    server: str,
    select: str | None,
    marker: str | None,
    channel: str,
    suite_root: str,
    cwd: Path | None = None,
) -> int:
    """Run the suite and report to Slack. Returns pytest's exit code.

    Called from a background task, so nothing here is on the three-second Slack
    budget — but it must also never raise into the task runner, because an
    unhandled exception there is a run that silently never reports.
    """
    cwd = cwd or Path.cwd()
    slack = SlackNotifier(channel=channel)

    # Defence in depth: these were validated in the parser, but this function is
    # importable and a future caller may not go through it.
    for value, name in ((select, "select"), (marker, "marker")):
        if value is not None and not EXPRESSION.match(value):
            slack.post(f"Refused `{name}`: contains disallowed characters.")
            return 4

    suite = cwd / suite_root / product
    if not suite.is_dir():
        slack.post(f"No suite at `{suite_root}/{product}` — nothing to run.")
        return 5

    argv = build_argv(product, server, select, marker, suite_root)
    slack.start(f"Starting `{product}` on `{server}`\n`{' '.join(argv[-6:])}`")

    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15 * 60,
            check=False,
        )
        code, out = completed.returncode, completed.stdout
    except subprocess.TimeoutExpired:
        slack.update("⏱️ Run exceeded 15 minutes and was killed.", force=True)
        return 3
    except OSError as exc:
        slack.update(f"Could not start pytest: {exc}", force=True)
        return 3

    duration = time.monotonic() - started
    passed, failed, skipped = _counts(out)

    # NOTE: `out` is captured but deliberately NOT posted. A suite's stdout in a
    # channel is how a channel gets muted, and it is also how hostnames and
    # stack traces reach a searchable, wide audience. Summary here; detail in
    # results.xml.
    slack.finish(passed=passed, failed=failed, skipped=skipped, duration=duration)
    log.info("pytest exited %s in %.1fs", code, duration)
    return code


def _counts(stdout: str) -> tuple[int, int, int]:
    """Pull pass/fail/skip counts out of pytest's summary line.

    Parsing stdout is a compromise the real implementation should not make — the
    reporter plugin on the page hooks pytest directly and gets exact numbers.
    This keeps V1 to a single subprocess with no plugin to install, and it is
    honest about being approximate: an unparseable summary yields zeros rather
    than a wrong number, and the exit code is what actually decides pass/fail.
    """
    import re

    passed = failed = skipped = 0
    for count, word in re.findall(r"(\d+) (passed|failed|skipped|error[s]?)", stdout):
        n = int(count)
        if word == "passed":
            passed = n
        elif word == "skipped":
            skipped = n
        else:
            failed += n
    return passed, failed, skipped

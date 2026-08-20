"""Argv construction for V1. Pure, so cheap to cover exhaustively."""

import pytest

from slack_runtests.runners.local import build_argv, _counts

pytestmark = pytest.mark.unit


def test_suite_path_is_built_from_the_allowlisted_product() -> None:
    argv = build_argv("webapp", "staging", None, None, "tests/sample")
    assert "tests/sample/webapp" in argv
    assert "--server=staging" in argv


def test_optional_filters_are_omitted_when_absent() -> None:
    argv = build_argv("webapp", "dev", None, None, "tests/sample")
    assert "-k" not in argv and "-m" not in argv


def test_filters_are_passed_as_separate_argv_entries() -> None:
    # Separate entries, not "-k smoke and not slow" as one string — that would
    # arrive at pytest as a single unparseable argument.
    argv = build_argv("webapp", "dev", "smoke and not slow", "not slow", "tests/sample")
    assert argv[argv.index("-k") + 1] == "smoke and not slow"
    assert argv[argv.index("-m") + 1] == "not slow"


def test_argv_is_a_list_of_strings() -> None:
    # The property that makes shell=False safe. A string here plus shell=True
    # would make every value above injectable.
    argv = build_argv("webapp", "dev", None, None, "tests/sample")
    assert isinstance(argv, list) and all(isinstance(p, str) for p in argv)


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ("3 passed, 1 skipped in 0.40s", (3, 0, 1)),
        ("2 failed, 5 passed in 1.2s", (5, 2, 0)),
        ("1 error, 1 passed in 0.1s", (1, 1, 0)),
        ("no recognisable summary", (0, 0, 0)),
    ],
)
def test_count_parsing(summary: str, expected: tuple[int, int, int]) -> None:
    assert _counts(summary) == expected

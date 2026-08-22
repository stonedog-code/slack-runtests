"""A suite with a KNOWN, DELIBERATE outcome: 3 passed, 1 failed, 1 skipped.

THIS SUITE IS NOT PART OF ANY GATE, AND ONE OF ITS TESTS FAILS ON PURPOSE.

It is the fixture `tests/integration/test_local_runner.py` runs through the real
V1 runner — a real `subprocess`, a real pytest, real stdout — so that the count
parsing in `runners/local.py` can be checked against numbers decided here rather
than against numbers pytest happened to print. `runners/local.py` scrapes those
counts out of pytest's summary line instead of using a reporter plugin, so a
change to pytest's output format would make the reported numbers WRONG rather
than absent. Nothing but a real run can catch that.

`fixture_suite` is in `norecursedirs`, so the project's own gate never collects
this directory — the failing test below would otherwise turn the integration
tier red for the wrong reason. The runner reaches it by naming the `webapp`
directory directly, which starts collection below the excluded name.

CHANGING THE OUTCOME HERE MEANS CHANGING THE ASSERTIONS THERE. That coupling is
deliberate: the numbers are the point, so they are stated twice, in two files,
and a drift between them is a failure rather than a silent adjustment.
"""

import pytest


def test_alpha_passes(server: str) -> None:
    # The flag really did arrive, which is what proves argv survived the
    # subprocess boundary rather than merely being built correctly.
    assert server == "staging"


def test_beta_passes() -> None:
    assert True


def test_gamma_passes() -> None:
    assert True


def test_delta_fails() -> None:
    """Fails on purpose. See the module docstring before 'fixing' this."""
    assert 1 == 2, "deliberate failure: this suite exists to produce one"


@pytest.mark.skip(reason="deliberately skipped, so the skip count is non-zero")
def test_epsilon_is_skipped() -> None:
    ...

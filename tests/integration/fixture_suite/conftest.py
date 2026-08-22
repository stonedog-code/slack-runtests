"""The `--server` flag the V1 runner passes, for the fixture suite.

Without it `pytest --server=staging` dies with "unrecognized arguments" and the
run reports a usage error rather than a result — which reads as a broken command
and is really a missing conftest. `tests/sample` carries the same file for the
same reason; this one is separate because the two suites exist for different
purposes and must be able to change independently.
"""

import pytest


def pytest_addoption(parser):
    parser.addoption("--server", default="local", help="environment under test")


@pytest.fixture(scope="session")
def server(request) -> str:
    return request.config.getoption("--server")

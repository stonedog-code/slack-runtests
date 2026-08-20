"""Adds the --server flag the runner passes.

Without this, `pytest --server=staging` dies with "unrecognized arguments" and
the Slack message reports a usage error rather than a test result — which reads
as a broken command and is really a missing conftest.
"""

import pytest


def pytest_addoption(parser):
    parser.addoption("--server", default="local", help="environment under test")


@pytest.fixture(scope="session")
def server(request) -> str:
    return request.config.getoption("--server")

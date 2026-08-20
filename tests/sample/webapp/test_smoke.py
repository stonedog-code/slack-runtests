"""The demo suite the Slack command actually runs.

Shows the intended usage of `slack.py` from inside a test: with no
SLACK_BOT_TOKEN these calls print what they would have sent and to which
channel, so the suite is safe to run anywhere.
"""

import pytest

from slack_runtests.slack import notify

pytestmark = pytest.mark.smoke


def test_homepage_responds(server: str) -> None:
    notify(f"Checking homepage on {server}")
    assert server in ("local", "dev", "staging")


def test_login_form_present(server: str) -> None:
    assert True


def test_search_returns_results(server: str) -> None:
    # A test reporting to a non-default channel.
    notify("Search index looks healthy", channel="#search-team")
    assert True


@pytest.mark.skip(reason="pending fixture data")
def test_export_csv() -> None:
    ...

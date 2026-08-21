"""The edge server: validates Slack requests, queues work, hands it to test servers.

It runs no tests and it never calls the Slack API. Both of those are deliberate
and both are load-bearing — see README.md in this directory.
"""

__all__ = ["app"]

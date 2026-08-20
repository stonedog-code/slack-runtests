"""Run a pytest suite from a Slack slash command.

V1 (`RUNTESTS_MODE=local`)  — the API runs pytest on this machine.
V2 (`RUNTESTS_MODE=github`) — the API dispatches a GitHub Actions workflow and
                              a self-hosted runner does the work.
"""

__version__ = "0.1.0"

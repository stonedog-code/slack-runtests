"""`slack-runtests` — start the API server."""

from __future__ import annotations

import logging
import os

from .config import load
from .slack import announce_configuration


def main() -> int:
    import uvicorn

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(levelname)-8s %(name)s: %(message)s",
    )

    # Say up front whether results will actually reach Slack. This server runs
    # the tests itself (V1) and reports through SlackNotifier, so an operator
    # who starts it with no token gets a working test runner that posts
    # nowhere — and, without this line, no hint of that until the first run.
    cfg = load()
    announce_configuration(logging.getLogger("slack-runtests"), cfg.default_channel)

    uvicorn.run(
        "slack_runtests.api:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8500")),
        reload=bool(os.environ.get("RELOAD")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

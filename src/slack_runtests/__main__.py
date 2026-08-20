"""`slack-runtests` — start the API server."""

from __future__ import annotations

import logging
import os


def main() -> int:
    import uvicorn

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(levelname)-8s %(name)s: %(message)s",
    )
    uvicorn.run(
        "slack_runtests.api:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8500")),
        reload=bool(os.environ.get("RELOAD")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

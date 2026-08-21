"""`slack-runtests-runner` — start a test server."""

from __future__ import annotations

import logging
import os


def main() -> int:
    from .agent import run_forever

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(levelname)-8s %(name)s: %(message)s",
    )
    return run_forever()


if __name__ == "__main__":
    raise SystemExit(main())

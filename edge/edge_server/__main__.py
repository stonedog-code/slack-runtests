"""`slack-runtests-edge` — start the edge server."""

from __future__ import annotations

import logging
import os

from slack_runtests import identity

from .config import load


def main() -> int:
    import uvicorn

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(levelname)-8s %(name)s: %(message)s",
    )
    log = logging.getLogger("edge")
    cfg = load()

    # Print the edge's own fingerprint at startup. This is what an operator
    # compares against the value pinned in each test server's config, and it is
    # far easier to check a line of log output than to go and read a key file.
    key = identity.load_or_create(cfg.key_path)
    log.info("edge identity %s (key %s)", identity.fingerprint(identity.public_b64(key)), cfg.key_path)

    if not cfg.signing_secret:
        log.warning("SLACK_SIGNING_SECRET unset — Slack requests will be accepted UNVERIFIED")
    if cfg.enroll_token:
        log.warning("RUNNER_ENROLL_TOKEN is set — unknown test servers can self-enrol")
    if not cfg.admin_token:
        log.info("EDGE_ADMIN_TOKEN unset — /admin/fleet will 404")

    uvicorn.run(
        "edge_server.app:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8500")),
        reload=bool(os.environ.get("RELOAD")),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

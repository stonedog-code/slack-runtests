#!/usr/bin/env bash
# run.sh — start the API (or this project's own tests) on either machine.
#
#     bash run.sh                    # serve on :8500 (V1, local mode)
#     bash run.sh serve              # same
#     bash run.sh test               # this project's own 60-test unit suite
#
# Environment passes through, so the V2 mode still works the documented way:
#
#     RUNTESTS_MODE=github bash run.sh
#
# Then, in a second terminal, poke it with the signed request:
#
#     bash test.sh
#
# Why this exists rather than a bare `uv run slack-runtests`: the workspace is a
# Samba share, so the Mac and the Linux box see the SAME `.venv`, which is a
# Linux one — and using it from macOS fails with a misleading
# "Failed to spawn: No such file or directory". See scripts/uv-env.sh for the
# mechanism. This picks the right per-platform environment and syncs it first.
#
# Written for bash 3.2 — that is what macOS ships as /bin/bash.

set -euo pipefail

cd "$(dirname "$0")"
# shellcheck source=scripts/uv-env.sh
. ./scripts/uv-env.sh

uv sync --quiet

cmd="${1:-serve}"
[ $# -gt 0 ] && shift

case "$cmd" in
  serve)
    printf 'serving from %s — mode=%s\n' "$UV_PROJECT_ENVIRONMENT" "${RUNTESTS_MODE:-local}"
    exec uv run slack-runtests "$@"
    ;;
  test)
    exec uv run pytest "$@"
    ;;
  *)
    printf 'usage: %s [serve|test] [args...]\n' "$0" >&2
    exit 2
    ;;
esac

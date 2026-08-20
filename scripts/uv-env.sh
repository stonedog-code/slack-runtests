# uv-env.sh — pick a per-platform uv environment directory. SOURCE, don't run:
#
#     . "$(dirname "$0")/scripts/uv-env.sh"
#
# A `.venv` is NOT portable between machines: it holds compiled extensions for
# one architecture, an absolute-path shebang in every console script, and a
# `bin/python` symlink into that machine's uv toolchain. Normally that does not
# matter, because each machine has its own checkout.
#
# It matters when one checkout is reachable from two machines at once — this
# project began life inside a Samba share where a Mac and a Linux box saw the
# same working tree, `.venv` included. Using the other machine's `.venv` fails
# like this:
#
#     error: Failed to spawn: `uvicorn`
#       Caused by: No such file or directory (os error 2)
#
# which names the wrong file. `uvicorn` is present; its shebang points at
# `.venv/bin/python`, that symlink targets an interpreter the other machine does
# not have, and execve reports a missing shebang interpreter as ENOENT against
# the script. Nothing in the message mentions the interpreter, so it reads as
# "uvicorn is not installed" — and re-running `uv sync` does not fix it.
#
# So macOS gets `.venv-macos` and everything else gets `.venv`. On a normal,
# single-machine clone the split costs nothing; on a shared checkout it is the
# difference between working and that error. An explicit UV_PROJECT_ENVIRONMENT
# from the caller always wins.

if [ -z "${UV_PROJECT_ENVIRONMENT:-}" ]; then
  case "$(uname -s)" in
    Darwin) UV_PROJECT_ENVIRONMENT=".venv-macos" ;;
    *)      UV_PROJECT_ENVIRONMENT=".venv" ;;
  esac
  export UV_PROJECT_ENVIRONMENT
fi

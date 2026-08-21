# ONE image for the edge and for all three test servers.
#
# Debian 12 "bookworm" via python:3.12-slim-bookworm — the house choice for
# every machine in this system, and the reason is that they should be the same.
# An earlier sketch of this harness used three different distributions to prove
# the code was portable. That is a different question from the one this harness
# asks: it exists to show that three IDENTICAL test servers share a queue
# correctly, and mixed base images would mean a scheduling bug and a musl
# packaging bug produce the same red result.
#
# Debian rather than Alpine specifically: glibc, which is what almost every
# production host and CI runner uses, so a wheel that installs here installs
# there. Slim rather than full: ~80 MB smaller and nothing in this stack needs
# a compiler at runtime.
FROM python:3.12-slim-bookworm

# uv manages the interpreter and the lockfile, exactly as it does on a laptop —
# pinned, because a moving tag makes two builds of the "same" image different.
COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /uvx /bin/

ENV UV_PROJECT_ENVIRONMENT=/app/.venv-docker \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    # Every `uv run` at container start installs from the lock and never
    # re-resolves. Without this a container can quietly drift from the image
    # it was built as.
    UV_FROZEN=1

WORKDIR /app

# Dependencies first, so editing source does not re-resolve the world.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY edge/ ./edge/
COPY test-server/ ./test-server/
COPY tests/ ./tests/

# --frozen: install exactly what the lockfile says. A container that silently
# resolves a different version from the laptop is a container that reproduces
# nothing.
#
# NOT --no-dev, and that is not laziness: pytest lives in the dev group, and a
# test server whose whole job is to run pytest needs it. `--no-dev` here builds
# an image that starts cleanly, enrols, claims a job, and then fails to run it.
RUN uv sync --frozen

# A non-root user, and it owns the key directory. The private key is written
# 0600 by identity.load_or_create; that mode means nothing if the process is
# root, because root ignores it.
RUN useradd --create-home --uid 10001 runner \
    && mkdir -p /app/keys /app/data \
    && chown -R runner:runner /app
USER runner


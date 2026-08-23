# Call Recording Splitter — run the CLI without installing Python, uv or ffmpeg.
#
#   docker build -t call-recording-splitter .
#   docker run -it --rm --user "$(id -u):$(id -g)" \
#       -v /path/to/calls:/data/input -v /path/to/parts:/data/output \
#       call-recording-splitter
#
# -it keeps rich's colours and live progress bars; --user keeps the parts owned
# by you instead of by a container user.

# --------------------------------------------------------------------------- #
# Build stage: resolve and install dependencies with uv.
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS builder

# uv, copied from its official image rather than fetched with an install script.
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, from the lockfile alone. This layer survives every source
# edit and is rebuilt only when pyproject.toml or uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the project itself. --no-editable copies the package into the venv, so
# the runtime stage needs nothing but the venv.
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# --------------------------------------------------------------------------- #
# Runtime stage: ffmpeg and the finished venv, no uv and no build leftovers.
# --------------------------------------------------------------------------- #
FROM python:3.12-slim

# ffmpeg brings ffprobe along. The apt lists are removed in the same layer so
# they never reach the image.
RUN apt-get update \
    && apt-get install --no-install-recommends --yes ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Docker sets TERM only with -t; this keeps rich in colour either way.
    TERM=xterm-256color \
    # Read by cli.in_container(): use the mounts instead of prompting.
    RUNNING_IN_DOCKER=1

# The two mount points, plus a non-root owner so a plain `docker run` does not
# write root-owned files into the output folder. `--user` overrides this.
# /app is left root-owned on purpose: the app only ever reads it.
RUN mkdir -p /data/input /data/output \
    && useradd --create-home --uid 1000 app \
    && chown -R app:app /data

USER app
VOLUME ["/data/input", "/data/output"]
WORKDIR /data

# Arguments after the image name go straight to the CLI, e.g. `--minutes 30`.
ENTRYPOINT ["/app/.venv/bin/split-recordings"]

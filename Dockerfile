FROM python:3.12-slim

# ansible-core's forked worker processes crash immediately ("ERROR! A worker
# was found in a dead state") without a UTF-8 locale, and python:3.12-slim
# sets none by default. C.UTF-8 is glibc's built-in locale — no `locales`
# package/locale-gen needed.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# git-crypt for encrypted .env file management (Phase C secrets tools).
# ansible-core is NOT installed here via apt deliberately: Debian's
# ansible-core package depends on (and forks its workers under) the
# distro's *system* Python — which drifted to 3.13 on this base image's
# Debian snapshot, independent of and untested against the 3.12 this
# project targets — and that pairing reproducibly crashed every
# hardware-discover-now run with "A worker was found in a dead state".
# It's a project dependency instead (see pyproject.toml), so it runs
# under the exact same pinned, tested Python 3.12 as the rest of the app.
RUN apt-get update && apt-get install -y --no-install-recommends git git-crypt && rm -rf /var/lib/apt/lists/*

# uv for reproducible, fast dependency installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the application source.
COPY src ./src
RUN uv sync --frozen --no-dev

# HTTP transport for multi-client use behind Traefik.
ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8765

EXPOSE 8765
VOLUME ["/data"]

# TCP liveness check on the MCP port. No HTTP /health endpoint exists yet
# (the MCP streamable-http transport doesn't expose one), so we settle for
# confirming the listener is up. Uses stdlib only — no extra system deps.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import socket, os; socket.create_connection(('127.0.0.1', int(os.environ.get('MCP_PORT', '8765'))), timeout=2).close()" || exit 1

CMD ["registry-mcp"]

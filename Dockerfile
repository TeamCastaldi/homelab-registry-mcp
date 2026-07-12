FROM python:3.12-slim

# ansible-core's forked worker processes crash immediately ("ERROR! A worker
# was found in a dead state") without a UTF-8 locale, and python:3.12-slim
# sets none by default. C.UTF-8 is glibc's built-in locale — no `locales`
# package/locale-gen needed.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# git-crypt for encrypted .env file management (Phase C secrets tools).
# ansible-core for hardware-discover-now's `ansible ... -m setup` fact-gather
# (Phase 9b) — the `setup` module it runs is a core module, so ansible-core
# (not the much larger `ansible` community metapackage bootstrap.sh installs
# on the host for the separate GitHub Actions runner) is enough here.
RUN apt-get update && apt-get install -y --no-install-recommends git git-crypt ansible-core && rm -rf /var/lib/apt/lists/*

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

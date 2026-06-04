# homelab-registry-mcp

A Model Context Protocol (MCP) server that keeps one authoritative catalog of
every service running in a homelab. It discovers services from Traefik,
Authentik, and Docker, flags services that are exposed without the
authentication they should have, and answers questions about the homelab through
MCP tools. It is for homelab operators who drive their lab from an MCP-capable
client such as Claude or VS Code and want a single source of truth they can both
query and act on.

## Features

### Read (always on)

- Discovers services from Traefik, Authentik, and Docker on a schedule and
  reconciles them into one registry, marking services stale (never deleting)
  when they disappear.
- Links a service across sources automatically — Traefik router, Authentik
  application, and Docker container — and returns the whole picture in one call.
- Flags auth conflicts: a service Authentik protects but Traefik does not
  enforce. The Authentik outpost sidecar pattern is recognised so protected
  services are not flagged by mistake.
- Read-only tools for Traefik and Authentik (routers, middlewares, applications,
  providers, outposts, policies, the audit log) plus a curated registry and
  append-only change and discovery logs.
- Optional LLM reasoning (off by default) for fuzzy cross-source matching,
  metadata enrichment, and access-audit summaries.

### Write (opt-in, off by default)

- Opens one pull request per security finding with a generated configuration
  fix, notifies you, and confirms the fix on the next discovery pass. The server
  writes to Git only — it never merges, deploys, or edits files directly, and a
  human reviews every change.

## How to run

The deployment model is a workload node running plain
`docker compose`, with the image served from a local registry.

### Prerequisites

- A host with Docker and the Compose plugin.
- Traefik running on an external Docker network named `traefik`, with a
  `websecure` TLS entrypoint and DNS for `registry-mcp.<your-domain>` pointing at it.
- A read-only Authentik service-account token (never an admin token).

### 1. Clone and configure

```bash
git clone <repo-url> homelab-registry-mcp
cd homelab-registry-mcp
cp .env.example .env
# Set at least TRAEFIK_API_URL, AUTHENTIK_API_URL, AUTHENTIK_TOKEN, DOCKER_BASE_URL.
```

`.env.example` documents every option. The write path and the reasoning layer
are off by default.

### 2. Pull the image

```bash
docker pull ghcr.io/teamcastaldi/homelab-registry-mcp:latest
```

### 3. Deploy on the target host

The committed `docker-compose.yml` builds locally by default; for the registry
flow, set its `image:` to the tag you pushed above, then:

```bash
docker compose pull
docker compose up -d
docker compose logs -f homelab-registry-mcp   # expect a scheduler_started line
```

### 4. Connect a client

The server is reachable at `https://registry-mcp.<your-domain>/mcp` over the
streamable-http transport.

In VS Code, add it to `.vscode/mcp.json`:

```json
{ "servers": { "homelab-registry": { "type": "http", "url": "https://registry-mcp.<your-domain>/mcp" } } }
```

In Claude Desktop, add an MCP server with the same URL under Settings.

## Documentation

- [docs/architecture.md](docs/architecture.md) — layout, the full tool surface,
  logging, seeding.
- [docs/development.md](docs/development.md) — local development and smoke test.
- [docs/agentic-design-intent.md](docs/agentic-design-intent.md) — design
  rationale and standing policies.

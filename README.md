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
- Hardware node inventory: register physical and virtual nodes with role, IP,
  CPU/RAM/storage specs, and storage-pool capacity; link services to nodes;
  query aggregate capacity across the lab.
- Optional LLM reasoning (off by default) for fuzzy cross-source matching,
  metadata enrichment, and access-audit summaries.

### Write (opt-in, off by default)

- Opens one pull request per security finding with a generated configuration
  fix, notifies you, and confirms the fix on the next discovery pass. The server
  writes to Git only — it never merges, deploys, or edits files directly, and a
  human reviews every change.
- Manages encrypted secrets in the homelab Git repo via `git-crypt`: read, add,
  rotate, and list keys in `.env` files without the operator touching the command
  line.

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

- [CLAUDE.md](CLAUDE.md) — project structure, architecture, all environment
  variables, key conventions, and current phase status. Start here.
- [docs/ARDs/ADR-001-Homelab-Control-Plane.md](docs/ARDs/ADR-001-Homelab-Control-Plane.md) — architecture, design decisions, and phased roadmap
- [docs/ARDs/ADR-002-Client-Interfaces.md](docs/ARDs/ADR-002-Client-Interfaces.md) — MCP client integration and Discord bot interface decisions
- [docs/ARDs/ARD-003-OOBE-Decisions.md](docs/ARDs/ARD-003-OOBE-Decisions.md) — out-of-box experience decisions
- [docs/ARDs/ARD-004-Upstream-Version-Detection-and-Update-Proposals.md](docs/ARDs/ARD-004-Upstream-Version-Detection-and-Update-Proposals.md) — upstream version detection and update proposal design
- [docs/SOPs/SOP-001-Deploy-New-Service.md](docs/SOPs/SOP-001-Deploy-New-Service.md) — runbook for deploying a new service to an onboarded node
- [docs/plans/phase-d.md](docs/plans/phase-d.md) — migration plan: Heimdall → Watchtower deployment with Traefik static backend
- [CONTRIBUTING.md](CONTRIBUTING.md) — branch naming, commit format, and the local checks to run before a PR
- [SECURITY.md](SECURITY.md) — security posture, supported versions, and how to report a vulnerability
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — expected conduct in project spaces

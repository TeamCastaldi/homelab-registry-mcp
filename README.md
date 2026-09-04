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
- Every hard delete of a service or hardware node is gated behind a solvable
  arithmetic challenge (request, solve, then confirm) — deliberate friction
  against an agent or a fat-fingered id removing something irreversible.
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
- Adopts a live, hand-run Docker service into GitOps management: SSHes into its
  host, sanitizes hardcoded secrets out of the original compose file, and pauses
  for you to choose whether to keep or rotate them before a PR is opened —
  nothing is committed until you decide.
- Closes the loop after a human merges a PR: a reusable GitHub Actions workflow
  and Ansible role (shipped here — your private homelab repo only holds config)
  redeploy the affected compose stack automatically.
- Sends a templated HTML email — PR summary, diff, Approve/Request Changes
  links — the moment a proposal PR opens, so you don't have to poll GitHub.

## How to run

See [docs/SETUP.md](docs/SETUP.md) for the full step-by-step setup guide.

This project ships an MCP server, not a node provisioner — it assumes you
already have a Docker host. The image is pulled from GHCR and no source
checkout is required on that host.

### Prerequisites

- A host with Docker and the Compose plugin.
- If you want it fronted by Traefik: the shipped `docker-compose.yml` carries
  Traefik Docker labels (`traefik.enable`, a `registry-mcp.<your-domain>`
  router, `websecure` entrypoint, TLS on with Traefik's default self-signed
  cert) and joins an external `traefik` Docker network. Create that network
  first (`docker network inspect traefik >/dev/null 2>&1 || docker network
  create traefik`) and make sure your Traefik instance is on it too
  (`--providers.docker=true`), plus DNS for `registry-mcp.<your-domain>`.
  Uncomment the commented-out
  `traefik.http.routers.registry-mcp.tls.certresolver` label and set it to
  your own Traefik's ACME resolver name for a real cert. Port
  8765 is also published directly for LAN/debug access regardless.
- A read-only Authentik service-account token (never an admin token).

### 1. Get the compose file and configure

Download just the two files you need — no full repo clone required:

```bash
VERSION=main  # or the latest tagged release, e.g. v0.26.1
mkdir homelab-registry-mcp && cd homelab-registry-mcp
curl -fsSL "https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/${VERSION}/docker-compose.yml" -o docker-compose.yml
curl -fsSL "https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/${VERSION}/.env.example" -o .env.example
cp .env.example .env
# Set at least TRAEFIK_API_URL, AUTHENTIK_API_URL, AUTHENTIK_TOKEN, DOCKER_BASE_URL.
# To pin the container image to the same release, add REGISTRY_MCP_VERSION=<same tag> to .env.
```

`.env.example` documents every option. The write path and the reasoning layer
are off by default.

### 2. Deploy on the target host

```bash
docker compose pull
docker compose up -d
docker compose logs -f homelab-registry-mcp   # expect a scheduler_started line
```

### Connect a client

The server is reachable at `https://registry-mcp.<your-domain>/mcp` over the
streamable-http transport.

In VS Code, add it to `.vscode/mcp.json`:

```json
{ "servers": { "homelab-registry": { "type": "http", "url": "https://registry-mcp.<your-domain>/mcp" } } }
```

In Claude Desktop, add an MCP server with the same URL under Settings.

## Documentation

- [docs/SETUP.md](docs/SETUP.md) — dedicated setup guide: prerequisites, the
  container deploy, connecting a client, pointing the server at your homelab
  config repo, hardware discovery, and troubleshooting.
- [CLAUDE.md](CLAUDE.md) — project structure, architecture, all environment
  variables, key conventions, and current phase status. Start here.
- [docs/ARDs/ADR-001-Homelab-Control-Plane.md](docs/ARDs/ADR-001-Homelab-Control-Plane.md) — architecture, design decisions, and phased roadmap
- [docs/ARDs/ADR-002-Client-Interfaces.md](docs/ARDs/ADR-002-Client-Interfaces.md) — MCP client integration and Discord bot interface decisions
- [docs/ARDs/ADR-003-OOBE-Decisions.md](docs/ARDs/ADR-003-OOBE-Decisions.md) — superseded by ADR-012; kept as the historical record of the OOBE design that was never implemented
- [docs/ARDs/ADR-004-Upstream-Version-Detection-and-Update-Proposals.md](docs/ARDs/ADR-004-Upstream-Version-Detection-and-Update-Proposals.md) — upstream version detection and update proposal design; its polling sources were never implemented, and ADR-010 advances it by push instead
- [docs/ARDs/ADR-005-Monitoring-Alerting-Recovery-Ingress-Architecture.md](docs/ARDs/ADR-005-Monitoring-Alerting-Recovery-Ingress-Architecture.md) — superseded by ADR-006; kept as the historical record of the removed monitoring stack
- [docs/ARDs/ADR-006-Pi-Non-MCP-Services-Komodo-Traefik.md](docs/ARDs/ADR-006-Pi-Non-MCP-Services-Komodo-Traefik.md) — Pi's non-MCP services (Komodo + Traefik), superseding ADR-005; deploy mechanism superseded by ADR-007
- [docs/ARDs/ADR-007-Komodo-Traefik-Move-To-GitOps.md](docs/ARDs/ADR-007-Komodo-Traefik-Move-To-GitOps.md) — Komodo/Traefik moved out of this repo's `docker-compose.yml` into GitOps-managed nodes
- [docs/ARDs/ADR-008-MCP-Tool-Organization.md](docs/ARDs/ADR-008-MCP-Tool-Organization.md) — draft: how the MCP tool surface is grouped and named
- [docs/ARDs/ADR-009-Conversational-Chat-Interface.md](docs/ARDs/ADR-009-Conversational-Chat-Interface.md) — superseded by ADR-011; kept as the reference design for the removed `/chat` interface
- [docs/ARDs/ADR-010-Dockhand-Update-Webhook.md](docs/ARDs/ADR-010-Dockhand-Update-Webhook.md) — Dockhand update/CVE alerts become staged proposals via `POST /webhooks/dockhand`
- [docs/ARDs/ADR-011-Remove-Komodo-Integration-And-Chat-Interface.md](docs/ARDs/ADR-011-Remove-Komodo-Integration-And-Chat-Interface.md) — withdraws the Komodo integration and the `/chat` interface from the server's supported surface
- [docs/ARDs/ADR-012-Scope-The-Repo-To-The-MCP-Server.md](docs/ARDs/ADR-012-Scope-The-Repo-To-The-MCP-Server.md) — removes the provisioning scripts; this repo ships the MCP server and the deploy action, not a node installer
- [docs/SOPs/SOP-001-Deploy-New-Service.md](docs/SOPs/SOP-001-Deploy-New-Service.md) — runbook for deploying a new service to an onboarded node
- [docs/SOPs/SOP-002-Connect-Dockhand-Webhook.md](docs/SOPs/SOP-002-Connect-Dockhand-Webhook.md) — runbook for pointing Dockhand at the update webhook
- [docs/plans/phase-d.md](docs/plans/phase-d.md) — historical: migration from workload node to a dedicated control-plane node. The migration itself is complete; its Traefik static-backend routing model is superseded by ADR-006/ADR-007, which co-locate Traefik on the same node behind standard Docker labels
- [CONTRIBUTING.md](CONTRIBUTING.md) — branch naming, commit format, and the local checks to run before a PR
- [SECURITY.md](SECURITY.md) — security posture, supported versions, and how to report a vulnerability
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — expected conduct in project spaces

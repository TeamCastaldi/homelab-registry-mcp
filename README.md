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
- Closes the loop after a human merges a PR: a reusable GitHub Actions workflow
  and Ansible role (shipped here — your private homelab repo only holds config)
  redeploy the affected compose stack automatically.
- Sends a templated HTML email — PR summary, diff, Approve/Request Changes
  links — the moment a proposal PR opens, so you don't have to poll GitHub.

## How to run

### Option A: fresh control-plane node (recommended)

For a brand-new Raspberry Pi (or other Debian/Ubuntu host) that will run
homelab-registry-mcp as its dedicated control plane, `scripts/install.sh` does
everything in one shot: installs Git, clones this repo, provisions the OS
(Docker, Ansible, `uv`, `git-crypt`, the GitHub CLI, an SSH key), prompts you for
the Traefik/Authentik/Git config and an optional DSPy (Advanced AI Reasoning)
opt-in, writes `.env`, brings the server up with `docker compose up -d`, and
only then applies a static IP — so the server is already running by the time
the SSH session drops.

```bash
VERSION=main  # or the latest tagged release, e.g. v0.11.0
bash -c "$(curl -fsSL https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/${VERSION}/scripts/install.sh)"
```

Every prompt can be pre-seeded with an environment variable of the same name
for non-interactive use. See [scripts/README.md](scripts/README.md) for the
full step-by-step and `scripts/bootstrap.sh`, the lower-level provisioning
script it calls.

### Option B: existing Docker host

If Docker is already set up on the host, skip straight to the compose file —
the image is pulled from GHCR and no source checkout is required.

#### Prerequisites

- A host with Docker and the Compose plugin.
- Traefik running on an external Docker network named `traefik`, with a
  `websecure` TLS entrypoint and DNS for `registry-mcp.<your-domain>` pointing at it.
- A read-only Authentik service-account token (never an admin token).

#### 1. Get the compose file and configure

Download just the two files you need — no full repo clone required:

```bash
VERSION=main  # or the latest tagged release, e.g. v0.11.0
mkdir homelab-registry-mcp && cd homelab-registry-mcp
curl -fsSL "https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/${VERSION}/docker-compose.yml" -o docker-compose.yml
curl -fsSL "https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/${VERSION}/.env.example" -o .env.example
cp .env.example .env
# Set at least TRAEFIK_API_URL, AUTHENTIK_API_URL, AUTHENTIK_TOKEN, DOCKER_BASE_URL.
# To pin the container image to the same release, add REGISTRY_MCP_VERSION=<same tag> to .env.
```

`.env.example` documents every option. The write path and the reasoning layer
are off by default.

#### 2. Deploy on the target host

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

- [CLAUDE.md](CLAUDE.md) — project structure, architecture, all environment
  variables, key conventions, and current phase status. Start here.
- [docs/ARDs/ADR-001-Homelab-Control-Plane.md](docs/ARDs/ADR-001-Homelab-Control-Plane.md) — architecture, design decisions, and phased roadmap
- [docs/ARDs/ADR-002-Client-Interfaces.md](docs/ARDs/ADR-002-Client-Interfaces.md) — MCP client integration and Discord bot interface decisions
- [docs/ARDs/ARD-003-OOBE-Decisions.md](docs/ARDs/ARD-003-OOBE-Decisions.md) — out-of-box experience decisions
- [docs/ARDs/ARD-004-Upstream-Version-Detection-and-Update-Proposals.md](docs/ARDs/ARD-004-Upstream-Version-Detection-and-Update-Proposals.md) — upstream version detection and update proposal design
- [docs/SOPs/SOP-001-Deploy-New-Service.md](docs/SOPs/SOP-001-Deploy-New-Service.md) — runbook for deploying a new service to an onboarded node
- [docs/plans/phase-d.md](docs/plans/phase-d.md) — migration plan: workload node → dedicated control-plane node deployment with Traefik static backend
- [CONTRIBUTING.md](CONTRIBUTING.md) — branch naming, commit format, and the local checks to run before a PR
- [SECURITY.md](SECURITY.md) — security posture, supported versions, and how to report a vulnerability
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — expected conduct in project spaces

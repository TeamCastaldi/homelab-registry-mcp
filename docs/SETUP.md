# Setup Guide

This is the step-by-step guide to getting `homelab-registry-mcp` running.
For a quick overview see the [README](../README.md); for environment variables,
architecture, and conventions see [CLAUDE.md](../CLAUDE.md).

This project ships an **MCP server**, not a node provisioner. It assumes you
already have a Docker host and, if you want the GitOps features, a private
homelab config repo. Provisioning the host, creating that repo, and building
its Ansible inventory are your infrastructure's concern, not this project's.

---

## 1. Prerequisites

- A host with Docker and the Compose plugin.
- If you want it fronted by Traefik: the shipped `docker-compose.yml` carries
  Traefik Docker labels (`traefik.enable`, a `registry-mcp.<your-domain>`
  router, `websecure` entrypoint, TLS with Traefik's default certificate) and
  joins an **external** Docker network named `traefik`. That network must
  already exist and your Traefik instance must be on it with
  `--providers.docker=true`, plus DNS for `registry-mcp.<your-domain>`. Create
  it once if it doesn't exist yet:

  ```bash
  docker network inspect traefik >/dev/null 2>&1 || docker network create traefik
  ```

  If you'd rather not use Traefik at all, drop the `networks:` block and the
  `labels:` block from `docker-compose.yml` and reach the server directly on
  port 8765.
- A **read-only** Authentik service-account token (never an admin token), if
  you want Authentik discovery.

## 2. Get the compose file and configure

```bash
VERSION=main  # or the latest tagged release, e.g. v0.26.1
mkdir homelab-registry-mcp && cd homelab-registry-mcp
curl -fsSL "https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/${VERSION}/docker-compose.yml" -o docker-compose.yml
curl -fsSL "https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/${VERSION}/.env.example" -o .env.example
cp .env.example .env
# Set at least TRAEFIK_API_URL, AUTHENTIK_API_URL, AUTHENTIK_TOKEN, DOCKER_BASE_URL.
# Also set MCP_ALLOWED_HOSTS to every Host clients use to reach /mcp, e.g.
# registry-mcp.<your-domain>,<LAN_IP>:8765 — any other Host gets HTTP 421.
# To pin the container image to the same release, add REGISTRY_MCP_VERSION=<same tag> to .env.
```

`.env.example` documents every option — see also the environment variable
table in [CLAUDE.md](../CLAUDE.md#environment-variables). The write path and
the reasoning layer are off by default.

## 3. Deploy

```bash
docker compose pull
docker compose up -d
docker compose logs -f homelab-registry-mcp   # expect a scheduler_started line
```

Only the one container image is pulled, from GHCR. No source checkout is
needed on the host.

Optionally seed the registry from a YAML file:

```bash
docker compose exec homelab-registry-mcp registry-mcp-seed /path/to/services.yaml
```

---

## Connecting an MCP client

The server is reachable at `https://registry-mcp.<your-domain>/mcp` over the
streamable-http transport (or `http://<host>:8765/mcp` if you haven't put it
behind Traefik).

Whichever address clients use has to be listed in `MCP_ALLOWED_HOSTS`. The
server checks every request's `Host` and `Origin` against DNS rebinding, as the
MCP spec requires, and the default allows only loopback names:

- The Traefik hostname goes in bare: `registry-mcp.<your-domain>`. A bare name
  matches requests on the default ports (80/443).
- A direct address needs its port: `<LAN_IP>:8765`, or `<LAN_IP>:*` for any
  port.
- Anything unlisted gets **HTTP 421**.

CLI and desktop clients (VS Code, Claude Desktop) send no `Origin` and are
unaffected by the origin check. A browser-based client also needs its origin
listed in `MCP_ALLOWED_ORIGINS`, or it gets **HTTP 403**.

In VS Code, add it to `.vscode/mcp.json`:

```json
{ "servers": { "homelab-registry": { "type": "http", "url": "https://registry-mcp.<your-domain>/mcp" } } }
```

In Claude Desktop, add an MCP server with the same URL under Settings.

## Connecting Traefik and Authentik

Don't guess at the values yourself — ask your MCP client to run
`discovery_connect_traefik` / `discovery_connect_authentik` (see
`src/registry_mcp/tools/discovery.py`). Each one live-tests the URL and
credentials and hands back the validated `.env` lines to add. Give each a plain
base URL, such as `http://traefik.lan:8080`; a URL with a query string or
fragment, or one that isn't http(s), is refused. `AUTHENTIK_TOKEN`
is the one exception: the tool never echoes it back (only a placeholder), so
you'll add that line with the token value yourself. Add the returned lines to
`.env` and restart — the tool never writes the file for you (the container has
no access to the host's `.env`) and never starts discovery immediately.

## Pointing at your homelab config repo

`hardware-discover-now`, the `secrets_*` tools, the proposal write path, and
brownfield adoption all read from (or open PRs against) a private Git repo you
control. **This project never creates that repo or its contents** — build it
however you like, then point the server at it.

What the repo needs to contain:

| Path | Used by | Notes |
|---|---|---|
| `nodes/<node>/<service>/compose.yaml` | proposal + normalization engines | The path template is configurable via `PROPOSAL_COMPOSE_PATH_TEMPLATE` |
| `**/.env` | `secrets_*` tools, adoption | git-crypt-encrypted, enforced by `.gitattributes` |
| `ansible.cfg` + an inventory | `hardware-discover-now` | Plain text; not encrypted |
| `.github/workflows/deploy.yml` | GitOps CD | A thin caller — see [the deploy pipeline](#the-deploy-pipeline) below |

Then add to `.env`:

```
SECRETS_REPO_PATH=/opt/homelab
SECRETS_KEY_PATH=/opt/homelab/.git-crypt.key
# OR, if you'd rather not keep the key as a file on disk:
# SECRETS_GIT_CRYPT_KEY=<base64 of the key, from your password manager>
ANSIBLE_CFG_PATH=/opt/homelab/ansible.cfg
SSH_KEY_PATH=/root/.ssh/id_ed25519
```

`ANSIBLE_CFG_PATH` and `SSH_KEY_PATH` are two of the three prerequisites
`system_health_check` looks for to leave read-only mode. Recreate the container
after editing (`docker compose up -d --force-recreate`) — a plain restart won't
reread `.env`.

The `secrets_*` tools can add secrets and list key names without further setup.
A few need more:

- **`secrets_decrypt`**, the only tool that returns a plaintext value to an MCP
  client, stays off until you add `SECRETS_ALLOW_DECRYPT=true`. Leave it off
  unless you need it; `secrets_list_keys` shows key names without it.
- **Re-locking:** both `secrets_decrypt` and `secrets_list_keys` lock the repo
  again after reading, if they were the ones to unlock it.
- **`secrets_rotate`** doesn't rotate the git-crypt key: git-crypt has no
  rotation command. It returns the manual steps instead.

> Back up your git-crypt key somewhere safe (Bitwarden, 1Password, …) before
> encrypting anything with it. If you lose it, every `.env` file it encrypts
> becomes unrecoverable.

Note that `GIT_TOKEN` in `.env` is a *different* credential from anything `gh`
holds on the host — it's used by registry-mcp's own code, inside the container,
to open PRs for the write path.

## Discovering your hardware

With `ANSIBLE_CFG_PATH` and `SSH_KEY_PATH` set and an inventory in place:

1. From an MCP client, call `hardware-discover-now` (optionally with
   `host: "<name-or-group>"` to target one node or group instead of the whole
   inventory). It runs `ansible <pattern> -m setup` over SSH and upserts each
   host's OS, CPU, RAM, and disks into the hardware registry as a `confirmed`
   `HardwareNode` — nothing is written back to the nodes themselves.
2. Re-run it any time (e.g. after adding a node to the inventory). It's
   idempotent, and any `display_name`/`role`/`tags`/`notes` you've set by hand
   via `hardware-update-node` are never overwritten.

Nodes registered manually via `hardware-add-node` stay as-is until the next
`hardware-discover-now` pass confirms them; `hardware-list-unconfirmed` and
`hardware-discovery-status` show what's still pending.

## The deploy pipeline

This repo ships the deploy *action* — `ansible/roles/docker-stack-deploy` and
a reusable `.github/workflows/deploy.yml`. Your homelab repo supplies only the
config and a thin caller workflow:

```yaml
# <your-homelab-repo>/.github/workflows/deploy.yml
name: Deploy
on:
  push:
    branches: [main]
jobs:
  deploy:
    uses: TeamCastaldi/homelab-registry-mcp/.github/workflows/deploy.yml@main
```

It runs on a self-hosted runner registered to *your* repo. See
[ansible/README.md](../ansible/README.md) for the full variable contract.

## Connecting Dockhand

If you run [Dockhand](https://github.com/Finsys/dockhand) for container
management, it can push its update and CVE-scan alerts here and have them become
staged pull requests instead of notifications you act on by hand. Off by default.

The full procedure is
[SOP-002](SOPs/SOP-002-Connect-Dockhand-Webhook.md). The short version:

```bash
DOCKHAND_WEBHOOK_ENABLED=true
DOCKHAND_WEBHOOK_SECRET=<a long random string>
```

Dockhand's own built-in webhook sender can't authenticate to this endpoint
directly — it borrows Apprise's URL scheme names without running the real
Apprise engine, so it can't deliver the header this secret needs. Reaching
it requires a small `caronc/apprise-api` sidecar in between, where the
header-carrying URL is actually honored, with Dockhand pointed at that
sidecar instead. SOP-002 walks through deploying and wiring it up; see
[ADR-010](ADRs/ADR-010-Dockhand-Update-Webhook.md) for why.

## Troubleshooting

- **Not sure which settings are missing, mistyped, or no longer needed?** Ask
  an MCP client to call `config_status`, or run
  `docker exec homelab-registry-mcp registry-mcp-config-check`. It lists every
  feature that's on but missing a setting it needs, environment keys that look
  like a typo of a real setting (`MCP_ALLOWED_HOST`) or belong to a removed
  feature (`KOMODO_*`), and settings set to their default anyway. It names
  settings only, never values, and the command exits non-zero when anything
  needs attention. Run it after every upgrade to find what still needs adding
  to your `.env` or Infisical.
- **`docker compose ps` never shows the container running** — check
  `docker compose logs homelab-registry-mcp` for a startup error; a missing or
  malformed `.env` value is the most common cause.
- **`network traefik declared as external, but could not be found`** — the
  shared Traefik network doesn't exist on this host yet. Create it (see
  [Prerequisites](#1-prerequisites)) or remove the `networks:`/`labels:` blocks
  from `docker-compose.yml`.
- **The server starts in read-only mode** — `system_health_check` reports which
  of the three prerequisites (Git repo, `ansible.cfg`, SSH key) is missing. The
  GitOps write tools stay disabled until all three resolve.
- **A `.env` change had no effect** — a plain `docker compose restart` doesn't
  reread `.env`. Use `docker compose up -d --force-recreate`.
- **An MCP client gets HTTP 421** — the `Host` it connects with isn't in
  `MCP_ALLOWED_HOSTS`. Add it (see
  [Connecting an MCP client](#connecting-an-mcp-client)) and recreate the
  container.
- **A browser-based client gets HTTP 403** — its `Origin` isn't in
  `MCP_ALLOWED_ORIGINS`.
- **`secrets_decrypt` says it's disabled** — set `SECRETS_ALLOW_DECRYPT=true`
  and recreate the container, or use `secrets_list_keys` if key names are
  enough.

## Related docs

- [CLAUDE.md](../CLAUDE.md) — architecture, full environment variable
  reference, and current project status
- [ansible/README.md](../ansible/README.md) — the deploy role's variable contract
- [docs/ADRs/ADR-001-Homelab-Control-Plane.md](ADRs/ADR-001-Homelab-Control-Plane.md) —
  design rationale for the control-plane architecture
- [docs/ADRs/ADR-010-Dockhand-Update-Webhook.md](ADRs/ADR-010-Dockhand-Update-Webhook.md) —
  design rationale for the Dockhand update webhook
- [docs/SOPs/SOP-002-Connect-Dockhand-Webhook.md](SOPs/SOP-002-Connect-Dockhand-Webhook.md) —
  step-by-step runbook for wiring Dockhand up

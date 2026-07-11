# Setup Guide

This is the step-by-step guide to getting `homelab-registry-mcp` running.
For a quick overview see the [README](../README.md); for environment variables,
architecture, and conventions see [CLAUDE.md](../CLAUDE.md).

## Which path do I want?

| | Option A: fresh control-plane node | Option B: existing Docker host |
|---|---|---|
| Use when | You have a spare Raspberry Pi, mini PC, or VM with a fresh Debian/Ubuntu install and nothing else on it | You already run Docker somewhere (NAS, server, existing homelab host) and just want the container |
| What it does | Provisions the whole node (packages, SSH key, static IP) *and* stands up the server | Only starts the container — you manage the host yourself |
| Time | ~10-15 minutes, one command | ~2 minutes if Docker is already set up |

If you're not sure, and you have a device to dedicate to this, use **Option A** —
it is the tested, documented path and does the most for you.

---

## Option A: Fresh control-plane node

### Prerequisites

- A Debian or Ubuntu host (Raspberry Pi OS Bookworm+, Debian 12, Ubuntu 22.04/24.04,
  x86_64 or ARM64 — VM or bare metal) with a fresh OS install and nothing important
  on it yet.
- SSH access to it with a sudo-capable user.
- Its current DHCP IP (for the initial SSH connection) and the static IP you want
  to give it long-term, on the same subnet.
- A GitHub account, if you plan to use the write path (opens PRs against your
  private homelab config repo) — you can skip this during install and add it later.

### The command

```bash
VERSION=main  # or the latest tagged release, e.g. v0.11.0
bash -c "$(curl -fsSL https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/${VERSION}/scripts/install.sh)"
```

Run this over SSH on the target node. It's interactive — you'll answer a handful
of prompts (see below) — but every prompt can be pre-seeded with an environment
variable of the same name (e.g. `GIT_PROVIDER=github`) for a non-interactive run.

### What it does

The command runs `scripts/install.sh`, which drives `scripts/bootstrap.sh` under
the hood. In order:

1. **Installs `git`** if it isn't already present (needed to clone the repo).
2. **Clones this repository** to `~/homelab-registry-mcp` (or a directory you
   choose when prompted). Re-running against an existing checkout pulls latest
   instead of re-cloning.
3. **Provisions the OS** by handing off to `bootstrap.sh --skip-network`, which
   installs:
   - **Docker** (`docker-ce`, `docker-ce-cli`, `containerd.io`,
     `docker-compose-plugin`) — runs the MCP server and any services it manages
   - **Ansible** + `ansible-lint` — powers the automated deploy pipeline
     (Phase 4 GitOps CD) once you connect your homelab repo
   - **`uv`** (via the official astral.sh installer) — the Python package
     manager `registry-mcp` itself uses
   - **`git-crypt`** — encrypts secrets (`.env` files) committed to your
     private homelab repo
   - **`gh`** (GitHub CLI) — used by the write path when `GIT_PROVIDER=github`
   - **NetworkManager** (if missing) — needed to apply the static IP in the
     last step
   - a handful of utility packages (`vim`, `htop`, `wget`, `nfs-common`,
     `net-tools`, `dnsutils`)

   It also sets the hostname to `homelab-control-plane`, generates an ED25519
   SSH key at `~/.ssh/id_ed25519` if one doesn't already exist (printing the
   public key so you can add it to GitHub), and creates `/mnt/appdata` and
   `/mnt/media` mount-point stubs. The static IP is *collected* here but not
   yet applied — see step 6.
4. **Prompts you for configuration** and writes `.env`:
   - Git provider for the write path (`github` or `gitea`, or blank to skip
     entirely — you can enable this later by hand)
   - If a provider is set: the repo (`owner/name`), a Git token, and the Git
     base URL
   - Whether to enable the optional DSPy reasoning layer, and your Anthropic
     API key if so

   This installer assumes a **greenfield** setup — no Traefik or Authentik yet
   — so it doesn't ask about them. Connect those once they exist (see
   [Connecting Traefik and Authentik later](#connecting-traefik-and-authentik-later)
   below).
5. **Starts the server**: `docker compose pull && docker compose up -d`, then
   waits for it to report running.
6. **Applies the static IP** last, by handing off to
   `bootstrap.sh --network-only` — this is deliberately the final step, so the
   server is already up and running by the time this drops your SSH session.
   Reconnect at the new IP afterward: `ssh <user>@<new-ip>`.

Everything above is idempotent — re-running the command on the same node skips
whatever's already installed or configured.

### After it finishes

- Reconnect: `ssh <your-user>@<the-static-ip-you-chose>`
- Check it's healthy: `docker compose logs -f homelab-registry-mcp` (look for a
  `scheduler_started` line) from the install directory
- [Connect an MCP client](#connecting-an-mcp-client)

---

## Option B: Existing Docker host

Use this if Docker is already running somewhere and you just want the
container — no OS provisioning, no source checkout.

### Prerequisites

- A host with Docker and the Compose plugin.
- Traefik running on an external Docker network named `traefik`, with a
  `websecure` TLS entrypoint and DNS for `registry-mcp.<your-domain>` pointing
  at it.
- A read-only Authentik service-account token (never an admin token), if you
  want Authentik discovery.

### 1. Get the compose file and configure

```bash
VERSION=main  # or the latest tagged release, e.g. v0.11.0
mkdir homelab-registry-mcp && cd homelab-registry-mcp
curl -fsSL "https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/${VERSION}/docker-compose.yml" -o docker-compose.yml
curl -fsSL "https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/${VERSION}/.env.example" -o .env.example
cp .env.example .env
# Set at least TRAEFIK_API_URL, AUTHENTIK_API_URL, AUTHENTIK_TOKEN, DOCKER_BASE_URL.
# To pin the container image to the same release, add REGISTRY_MCP_VERSION=<same tag> to .env.
```

`.env.example` documents every option — see also the environment variable
table in [CLAUDE.md](../CLAUDE.md#environment-variables). The write path and
the reasoning layer are off by default.

### 2. Deploy

```bash
docker compose pull
docker compose up -d
docker compose logs -f homelab-registry-mcp   # expect a scheduler_started line
```

No other software gets installed for you on this path — only the one
container image is pulled from GHCR.

---

## Connecting an MCP client

The server is reachable at `https://registry-mcp.<your-domain>/mcp` over the
streamable-http transport (or `http://<host>:8765/mcp` if you haven't put it
behind Traefik).

In VS Code, add it to `.vscode/mcp.json`:

```json
{ "servers": { "homelab-registry": { "type": "http", "url": "https://registry-mcp.<your-domain>/mcp" } } }
```

In Claude Desktop, add an MCP server with the same URL under Settings.

## Connecting Traefik and Authentik later

Both installer paths assume you may not have Traefik/Authentik set up yet.
Once they exist, don't hand-edit `.env` — ask your MCP client to run
`discovery_connect_traefik` / `discovery_connect_authentik` (see
`src/registry_mcp/tools/discovery.py`). Each one live-tests the URL and
credentials and hands back the exact `.env` lines to add, plus a restart —
they never write the file for you (the container has no access to the host's
`.env`) and never start discovery immediately.

## Troubleshooting

- **`docker compose ps` never shows the container running** — check
  `docker compose logs homelab-registry-mcp` for a startup error; a missing or
  malformed `.env` value is the most common cause.
- **Lost the SSH session after step 6 (Option A) and can't reconnect** — the
  static IP may not match what you entered, or the gateway/subnet was wrong.
  Reconnect via console/serial if available and re-run
  `bash scripts/bootstrap.sh --network-only`.
- **`nmcli` errors about an unmanaged interface** — Ubuntu Server defaults to
  netplan + systemd-networkd, not NetworkManager. Add `renderer: NetworkManager`
  to `/etc/netplan/*.yaml`, `sudo netplan apply`, then re-run bootstrap.
- **Re-running `install.sh`** is safe — it skips already-installed packages,
  pulls latest instead of re-cloning, and leaves an existing `.env` untouched
  rather than overwriting it.

## Related docs

- [scripts/README.md](../scripts/README.md) — what each script does, in brief
- [CLAUDE.md](../CLAUDE.md) — architecture, full environment variable
  reference, and current project status
- [docs/ARDs/ADR-001-Homelab-Control-Plane.md](ARDs/ADR-001-Homelab-Control-Plane.md) —
  design rationale and the full OOBE conversation flow (a later, conversational
  phase this guide's scripted path precedes)

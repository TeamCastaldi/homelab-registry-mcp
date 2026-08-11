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
export VERSION=main  # or the latest tagged release, e.g. v0.11.0
bash -c "$(curl -fsSL https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/${VERSION}/scripts/install.sh)"
```

Run this over SSH on the target node. It's interactive — you'll answer a handful
of prompts (see below) — but every prompt can be pre-seeded with an environment
variable of the same name (e.g. `GIT_PROVIDER=github`) for a non-interactive run.

`VERSION` pins the entire install, not just which copy of `install.sh` the
`curl` fetches: `install.sh` reads the same variable from its own environment
and clones the rest of the repo (`bootstrap.sh`, `scripts/`, `monitoring/`)
from that same ref, so a tagged release stays self-consistent end-to-end —
this also works with a branch name, not just a tag, if you're testing an
unreleased change. It must be `export`ed (as above), not just assigned: the
`curl` line's own `${VERSION}` expands correctly either way, but only an
exported `VERSION` reaches the `bash -c` subprocess that `install.sh` itself
runs as — a plain (non-exported) assignment still fetches the right
`install.sh`, but that script won't see `VERSION`, so its internal clone
silently falls back to `main`.

### What it does

The command runs `scripts/install.sh`, which drives `scripts/bootstrap.sh` under
the hood. Numbered here exactly as install.sh's own `[STEP N]` output numbers
them — including starting at 0 — so this list, the script's log output, and
its own top-of-file comment never drift out of sync with each other:

0. **Installs `git`** if it isn't already present (needed to clone the repo).
1. **Sparse-clones this repository** to `~/homelab-registry-mcp` (or a directory
   you choose when prompted) — root-level files (`docker-compose.yml`,
   `.env.example`, etc.) plus `scripts/`, skipping `src/`, `ansible/`, `tests/`,
   and other build/CI-time directories, since the app runs from the GHCR image
   rather than a source checkout. Re-running against an existing checkout pulls
   latest instead of re-cloning.
2. **Provisions the OS** by handing off to `bootstrap.sh --skip-network`, which
   installs:
   - **Docker** (`docker-ce`, `docker-ce-cli`, `containerd.io`,
     `docker-compose-plugin`) — runs the MCP server and any services it manages
   - **Ansible** + `ansible-lint` — powers the automated deploy pipeline
     (Phase 4 GitOps CD) once you connect your homelab repo
   - **`uv`** (via the official astral.sh installer) — the Python package
     manager `registry-mcp` itself uses
   - **`git-crypt`** — encrypts secrets (`.env` files) committed to your
     private homelab repo
   - **`gh`** (GitHub CLI) — used by the write path when `GIT_PROVIDER=github`,
     and by step 4 below to create your homelab config repo
   - **NetworkManager** (if missing) — needed to apply the static IP in the
     last step
   - a handful of utility packages (`vim`, `htop`, `wget`, `nfs-common`,
     `net-tools`, `dnsutils`)

   It also sets the hostname to `homelab-control-plane`, generates an ED25519
   SSH key at `~/.ssh/id_ed25519` if one doesn't already exist (printing the
   public key so you can add it to GitHub), and creates `/mnt/appdata` and
   `/mnt/media` mount-point stubs. The static IP is *collected* here but not
   yet applied — see step 8.
3. **Prompts you for configuration**:
   - Git provider for the write path (`github` or `gitea`, or blank to skip
     entirely — you can enable this later by hand)
   - If a provider is set: the repo (`owner/name`), a Git token, and the Git
     base URL
   - Whether to enable the optional DSPy reasoning layer, and your Anthropic
     API key if so
   - Whether to enable Komodo (container management, logs, update detection
     for this node) and/or Traefik (this node's central ingress) — ADR-006's
     two independent yes/no gates. If either is yes: your control-plane
     node's LAN IP/hostname. If Komodo: an admin username/password (password
     auto-generated if left blank) — everything else Komodo needs (database
     credentials, Core↔Periphery secrets) and Traefik's Redis password are
     always auto-generated, never prompted for.

   This installer assumes a **greenfield** setup — no Traefik discovery or
   Authentik yet — so it doesn't ask about connecting to an existing Traefik
   instance elsewhere in your lab. Connect those once they exist (see
   [Connecting Traefik and Authentik later](#connecting-traefik-and-authentik-later)
   below).
4. **Optionally creates your private homelab config repo** — the same one
   `scripts/setup-homelab-repo.sh` creates standalone, folded in here.
   `gh`/`git-crypt` not being on `PATH` skips this step with instructions
   rather than blocking the rest of the install (bootstrap.sh should have
   installed both). If `gh` isn't authenticated yet, this step offers to run
   the one-time `gh auth login` device-code login right here (safe over SSH —
   it prints a URL and code you open on any device's browser); declining, or
   the login not completing, skips the rest of this step the same way. If you
   already answered the Git
   provider prompt in step 3 with `github`, this reuses that same
   `owner/name` instead of asking again — otherwise it asks for a repo name
   and creates it under your account. Creates the target directory with
   `sudo` first if it isn't writable by your user (the `/opt` default
   commonly isn't, for a non-root operator). Clones it, initialises
   `git-crypt`, writes `.gitattributes` (encrypts `**/.env`) and a
   `.gitignore` entry for the exported key if it lands inside the repo (the
   Pi default does), scaffolds `nodes/`, exports the git-crypt key, commits,
   and pushes — a push failure only warns, it doesn't abort the rest of the
   installer. **Back up the printed key to your password manager before
   doing anything else** — it's the only way to decrypt secrets if this node
   is lost.
5. **Sets up the Ansible inventory, if a homelab config repo now exists** at
   `SECRETS_REPO_PATH` (`/opt/homelab` by default — either just created in
   step 4, or one you already had). If it does, and you opt in: writes
   `ansible.cfg` + `ansible/inventory.yml`, seeds this node into the
   inventory itself (auto-detects hostname/LAN IP, authorizes its own SSH
   key over real SSH — not a local connection, so it's reachable the same
   way any other host is), then prompts for more hosts (blank name to
   finish). Commits and pushes — a push failure only warns, it doesn't abort
   the rest of the installer. This is what gives `hardware-discover-now` a
   real, verified node to fact-gather once the server is up. No homelab repo
   at all → this step prints how to run it later and skips cleanly; nothing
   else in the installer depends on it.
6. **Writes `.env`** from everything collected in steps 3–5.
7. **Starts the server**: `docker compose pull && docker compose up -d`, then
   waits for it to report running. Anything enabled above (Komodo, Traefik,
   the homelab repo, the Ansible inventory) comes up or takes effect in this
   same step, via the `.env` step 6 just wrote.
8. **Applies the static IP** last, by handing off to
   `bootstrap.sh --network-only` — this is deliberately the final step, so the
   server is already up and running by the time this drops your SSH session.
   Reconnect at the new IP afterward: `ssh <user>@<new-ip>`.

Everything above is idempotent — re-running the command on the same node skips
whatever's already installed or configured.

### After it finishes

- Reconnect: `ssh <your-user>@<the-static-ip-you-chose>`
- Check it's healthy: `docker compose logs -f homelab-registry-mcp` (look for a
  `scheduler_started` line) from the install directory
- [Set up your homelab config repo](#setting-up-your-homelab-config-repo)
- [Discover your hardware](#discovering-your-hardware)
- [Connect an MCP client](#connecting-an-mcp-client)

---

## Option B: Existing Docker host

Use this if Docker is already running somewhere and you just want the
container — no OS provisioning, no source checkout.

### Prerequisites

- A host with Docker and the Compose plugin.
- Traefik reachable from this host, if you want it fronted by Traefik. The
  shipped `docker-compose.yml` publishes port 8765 directly and does not join
  a Docker network — Traefik routes to it via a static backend (an IP:port
  entry in Traefik's dynamic file config), not Docker label discovery. Point a
  `websecure` TLS entrypoint and DNS for `registry-mcp.<your-domain>` at
  `<this-host>:8765`.
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

## Setting up your homelab config repo

`hardware-discover-now`, the `secrets_*` tools, and the automated deploy
pipeline all read from (or write to) a private Git repo you control —
`SECRETS_REPO_PATH`, `/opt/homelab` by default. This project never creates
that repo or its contents for you; do this once before
[Discovering your hardware](#discovering-your-hardware) below.

### 1. Authenticate the GitHub CLI

`bootstrap.sh` installs the `gh` binary, but can't log it in for you — unlike
the plain `read -rp` text prompts `install.sh` asks, `gh auth login` opens a
browser OAuth flow or a device-code flow, either of which needs a human to
click through somewhere, and it writes to its own credential store
(`~/.config/gh/hosts.yml`), not `.env`. Run it once, manually:

```bash
gh auth login
```

Don't confuse this with `GIT_TOKEN` in `.env` — that's a *different*
credential, used by registry-mcp's own code (inside the container) to open
PRs for the Phase 8 write path. Authenticating `gh` on the host doesn't set
`GIT_TOKEN`, and setting `GIT_TOKEN` doesn't authenticate `gh`.

### 2. Create the repo (first time only), or re-clone it (every other time)

The very first time, on any machine:

```bash
scripts/setup-homelab-repo.sh
```

This creates a private GitHub repo, clones it to `/opt/homelab`, initializes
git-crypt, and exports the encryption key — follow its printed instructions
to back that key up (Bitwarden, 1Password, etc.) *before* doing anything
else. If you lose it, every `.env` file it encrypts becomes unrecoverable.

If the repo already exists on GitHub (e.g. you're re-provisioning a node, or
reflashing an SD card) **don't re-run `setup-homelab-repo.sh`** — it creates
the repo itself, not just the local clone, and running it again against an
existing repo is not what you want. Just re-clone:

```bash
gh repo clone <your-github-user>/homelab /opt/homelab
```

`ansible.cfg`/`ansible/inventory.yml` aren't git-crypt-encrypted (only
`**/.env` files are, per `.gitattributes`), so they're readable immediately
from a plain clone — you only need the git-crypt key restored (from wherever
you backed it up in step 2 above) if you also want the `secrets_*` tools or
adoption features working on this node.

### 3. Point registry-mcp at it

Add to `.env` (adjust the path to wherever you cloned it):

```
SECRETS_REPO_PATH=/opt/homelab
SECRETS_KEY_PATH=/opt/homelab/.git-crypt.key
# OR, if you'd rather not keep the key as a file on disk:
# SECRETS_GIT_CRYPT_KEY=<base64 of the key, from your password manager>
```

Recreate the container (`docker compose up -d --force-recreate`), then
continue to [Discovering your hardware](#discovering-your-hardware).

## Discovering your hardware

Once your homelab config repo is set up (previous section), the next step is
to have the server fact-gather the nodes it's going to manage, rather than
typing each one in by hand — control-plane path only, it needs the SSH key
`install.sh` set up:

1. Make sure `ansible.cfg` and an inventory listing your nodes exist in your
   homelab config repo (the OOBE CLI that will generate these automatically
   is planned but not built yet — [ADR-001](ARDs/ADR-001-Homelab-Control-Plane.md)
   step 7). Run `scripts/setup-ansible-inventory.sh` from the control-plane
   node to bootstrap them: it seeds the inventory with the control-plane node
   itself (auto-detected, connects over SSH to its own LAN IP like any other
   host — so it needs its own key authorized on itself too), then prompts
   you for any other hosts to add — and for each one, runs `ssh-copy-id` so
   its SSH key is actually authorized there (falling back to printing the
   manual command if that fails). Idempotent — re-run it any time to add
   more.
2. Set `ANSIBLE_CFG_PATH` and `SSH_KEY_PATH` in `.env` — the script prints the
   exact values to use — and recreate the container
   (`docker compose up -d --force-recreate`; a plain restart won't reread
   `.env`). These are also the two prerequisites `system_health_check` looks
   for to leave read-only mode.
3. From an MCP client, call the `hardware-discover-now` tool (optionally with
   `host: "<name-or-group>"` to target one node/group instead of the whole
   inventory). It runs `ansible <pattern> -m setup` over SSH and upserts each
   host's OS, CPU, RAM, and disks into the hardware registry as a `confirmed`
   `HardwareNode` — nothing is written back to the nodes themselves.
4. Re-run it any time (e.g. after adding a node to the inventory) — it's
   idempotent, and any `display_name`/`role`/`tags`/`notes` you've set by hand
   via `hardware-update-node` are never overwritten.

Nodes registered manually via `hardware-add-node` stay as-is until the next
`hardware-discover-now` pass confirms them; `hardware-list-unconfirmed` and
`hardware-discovery-status` show what's still pending.

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
Once they exist, don't guess at the values yourself — ask your MCP client to
run `discovery_connect_traefik` / `discovery_connect_authentik` (see
`src/registry_mcp/tools/discovery.py`) first. Each one live-tests the URL and
credentials and hands back the validated `.env` lines to add. `AUTHENTIK_TOKEN`
is the one exception: the tool never echoes it back (only a placeholder), so
you'll add that line with the token value yourself. Add the returned lines to
`.env` and restart — the tool never writes the file for you (the container has
no access to the host's `.env`) and never starts discovery immediately.

## Troubleshooting

- **`docker compose ps` never shows the container running** — check
  `docker compose logs homelab-registry-mcp` for a startup error; a missing or
  malformed `.env` value is the most common cause.
- **Lost the SSH session after step 8 (Option A) and can't reconnect** — the
  static IP may not match what you entered, or the gateway/subnet was wrong.
  Reconnect via console/serial if available and re-run
  `bash scripts/bootstrap.sh --network-only`.
- **`nmcli` errors about an unmanaged interface** — something else already owns
  the interface. On Ubuntu Server this is usually netplan + systemd-networkd:
  add `renderer: NetworkManager` to `/etc/netplan/*.yaml`, `sudo netplan
  apply`, then re-run bootstrap. On Debian/Raspberry Pi OS it's usually
  ifupdown via `/etc/network/interfaces` instead: set `managed=true` under the
  `[ifupdown]` section in `/etc/NetworkManager/NetworkManager.conf`, `sudo
  systemctl restart NetworkManager`, then re-run bootstrap. `bootstrap.sh`
  Phase 6 detects which of the two applies and prints the matching guidance
  in its error message. If you're inside a container (LXC, etc.), this
  doesn't apply to you — see below.
- **Running Option A inside an LXC container (e.g. a Proxmox community-scripts
  Ubuntu template)** — `bootstrap.sh` detects this and automatically skips the
  step 8 static-IP application, since a container's address is normally owned
  by the host (Proxmox's own `net0` config for that container), not the guest.
  You'll see a "network owned by host" completion message instead of an SSH
  drop. If the earlier install already errored out before this detection was
  added, that's fine — steps 0-7 (packages, `.env`, the running server) still
  completed; only the redundant network step failed, and you can ignore it.
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

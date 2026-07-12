# Scripts

Dev-time and operational utilities that support `homelab-registry-mcp` but are not
part of the running server. Application code lives in `src/registry_mcp/`; these
scripts are tools for the operator/developer.

For a step-by-step walkthrough of running `install.sh`/`bootstrap.sh` — including
exactly what gets installed and what you'll be prompted for — see
[docs/SETUP.md](../docs/SETUP.md).

## What's here

- **`install.sh`** — the recommended one-shot entry point for a fresh control-plane
  node: sparse-clones root-level files (`docker-compose.yml`, `.env.example`, etc.)
  plus `scripts/`, skipping `src/`, `ansible/`, `tests/`, and other build/CI-time
  directories (the app runs from the GHCR image, not a source checkout), runs
  `bootstrap.sh --skip-network`,
  prompts for the Git secrets and an optional DSPy opt-in, writes `.env`, brings
  the MCP server up with `docker compose up -d`, and only then applies the
  static IP (`bootstrap.sh --network-only`) so the server is already running
  when the SSH session drops. Designed to be run via
  `curl -fsSL <raw-url>/scripts/install.sh | bash`; every prompt can be pre-seeded
  with an environment variable of the same name for non-interactive use.
  Assumes a greenfield setup — it deliberately doesn't ask about Traefik or
  Authentik, since a fresh homelab won't have those yet. Connect them later via
  the `discovery_connect_traefik` / `discovery_connect_authentik` MCP tools.
- **`bootstrap.sh`** — prepares a fresh node for the homelab control plane:
  installs Docker, Ansible, `uv`, `git-crypt`, and the GitHub CLI, sets the
  hostname, generates an SSH key, and applies a static IP. Supports Debian and
  Ubuntu (ADR-001 §3.1) on any hardware — Raspberry Pi or an x86_64/ARM64 VM —
  detecting the OS, Docker apt repo, network interface, and hardware type at
  runtime rather than assuming a Pi. Run directly for a bare provisioning pass,
  or let `install.sh` drive it —
  `bash scripts/bootstrap.sh [--dry-run] [--skip-network] [--network-only]`.
- **`reset-node.sh`** — factory-resets a control-plane node previously set up by
  `install.sh`/`bootstrap.sh`, without re-flashing the SD card: stops containers
  and wipes Docker volumes, deletes the repo checkout (`INSTALL_DIR`, default
  `~/homelab-registry-mcp`), removes the `/mnt/appdata`/`/mnt/media` stubs (only
  if empty), removes the generated SSH key, reverts the hostname (default
  `raspberrypi`), and deletes the static NetworkManager profile in favor of
  DHCP — the last step, since it drops the SSH session, same as
  `bootstrap.sh`. `--purge-packages` additionally removes the packages
  `bootstrap.sh` installed (Docker, Ansible, `git-crypt`, `gh`, `uv`);
  `--wipe-secrets` additionally deletes the git-crypt secrets repo and its
  exported key, gated behind its own typed confirmation since that key is the
  only local copy and losing it makes encrypted secrets unrecoverable. Neither
  flag is on by default. `bash scripts/reset-node.sh --dry-run` to preview.
- **`setup-homelab-repo.sh`** — one-time bootstrap of the private homelab Git repo
  (Phase C): creates the repo, initialises `git-crypt`, configures `.gitattributes`
  to encrypt `**/.env`, scaffolds `nodes/`, and exports the key. Backs the
  `secrets_*` MCP tools. Cross-platform (macOS, Linux, Windows via WSL/Git Bash);
  defaults to `$HOME`-relative paths (`$HOME/homelab`,
  `$HOME/.config/homelab/git-crypt.key`) so it runs without root on a laptop —
  override via `SECRETS_REPO_PATH` / `SECRETS_KEY_PATH` for the Pi (`/opt/homelab`).
- **`setup-ansible-inventory.sh`** — bootstraps (or extends) `ansible.cfg` +
  `ansible/inventory.yml` inside your homelab config repo (Phase 9b): both the
  reusable `.github/workflows/deploy.yml` and the `hardware-discover-now` MCP
  tool expect these to already exist and neither Ansible nor this project
  generates them for you. Seeds the inventory with the control-plane node
  itself (auto-detecting hostname/IP, marked `ansible_connection: local` — no
  SSH loopback to itself), then interactively prompts for more hosts (blank
  name to stop). Also prompts for the SSH private key Ansible should use and
  runs `ssh-copy-id` against each host you add — `ssh-keygen` only creates
  the key pair locally, nothing else authorizes it on a target — falling
  back to printing the manual command if that fails or the key's `.pub`
  file is missing. Commits and pushes when done. Idempotent: safe to re-run
  any time you want to add hosts; skips any host already present by name and
  leaves an existing `ansible.cfg` untouched. Run from the control-plane
  node: `scripts/setup-ansible-inventory.sh`.

## What belongs here

- Environment / node setup and provisioning helpers
- One-off migration or cleanup scripts (keep even after use — they document what
  was done)
- Local development conveniences

## What does not belong here

- Application code (that goes in `src/registry_mcp/`)
- Test files (those go in `tests/`)
- CI/CD pipeline definitions (those go in `.github/workflows/`)
- The deploy automation itself — the `docker-stack-deploy` Ansible role and
  playbook live in `ansible/`, invoked by the reusable
  `.github/workflows/deploy.yml` (Phase 4, see `CLAUDE.md`)

## Conventions

- Name scripts clearly and include a comment block at the top explaining what the
  script does, when to use it, and any required environment variables (both
  existing scripts follow this).
- Never hardcode secrets — read them from environment variables or `.env`.
- Note any platform assumptions at the top. `bootstrap.sh` targets Debian or
  Ubuntu control-plane nodes (Pi or VM, ADR-001 §3.1) and detects OS/interface/
  hardware rather than hardcoding them; `setup-homelab-repo.sh` is
  cross-platform (macOS, Linux, Windows via WSL) and is meant to run from a
  developer laptop as well as the control-plane node.

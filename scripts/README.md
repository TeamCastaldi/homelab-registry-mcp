# Scripts

Dev-time and operational utilities that support `homelab-registry-mcp` but are not
part of the running server. Application code lives in `src/registry_mcp/`; these
scripts are tools for the operator/developer.

## What's here

- **`install.sh`** — the recommended one-shot entry point for a fresh control-plane
  node: clones the repo, runs `bootstrap.sh --skip-network`, prompts for the
  Traefik/Authentik/Git secrets and an optional DSPy opt-in, writes `.env`, brings
  the MCP server up with `docker compose up -d`, and only then applies the static
  IP (`bootstrap.sh --network-only`) so the server is already running when the SSH
  session drops. Designed to be run via
  `curl -fsSL <raw-url>/scripts/install.sh | bash`; every prompt can be pre-seeded
  with an environment variable of the same name for non-interactive use.
- **`bootstrap.sh`** — prepares a fresh control-plane node as the homelab control
  plane: installs Docker, Ansible, `uv`, `git-crypt`, and the GitHub CLI, sets the
  hostname, generates an SSH key, and applies a static IP. Supports Debian and
  Ubuntu (ADR-001 §3.1) on any hardware — Raspberry Pi or an x86_64/ARM64 VM —
  detecting the OS, Docker apt repo, network interface, and hardware type at
  runtime rather than assuming a Pi. Run directly for a bare provisioning pass,
  or let `install.sh` drive it —
  `bash scripts/bootstrap.sh [--dry-run] [--skip-network] [--network-only]`.
- **`setup-homelab-repo.sh`** — one-time bootstrap of the private homelab Git repo
  (Phase C): creates the repo, initialises `git-crypt`, configures `.gitattributes`
  to encrypt `**/.env`, scaffolds `nodes/`, and exports the key. Backs the
  `secrets_*` MCP tools. Cross-platform (macOS, Linux, Windows via WSL/Git Bash);
  defaults to `$HOME`-relative paths (`$HOME/homelab`,
  `$HOME/.config/homelab/git-crypt.key`) so it runs without root on a laptop —
  override via `SECRETS_REPO_PATH` / `SECRETS_KEY_PATH` for the Pi (`/opt/homelab`).

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

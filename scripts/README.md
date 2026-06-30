# Scripts

Dev-time and operational utilities that support `homelab-registry-mcp` but are not
part of the running server. Application code lives in `src/registry_mcp/`; these
scripts are tools for the operator/developer.

## What's here

- **`bootstrap.sh`** — prepares a fresh Raspberry Pi (Debian Trixie) as the
  homelab control-plane node: installs Docker, Ansible, `uv`, `git-crypt`, and the
  GitHub CLI, sets the hostname, generates an SSH key, and applies a static IP.
  Run once after imaging — `bash scripts/bootstrap.sh [--dry-run]`.
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

## Conventions

- Name scripts clearly and include a comment block at the top explaining what the
  script does, when to use it, and any required environment variables (both
  existing scripts follow this).
- Never hardcode secrets — read them from environment variables or `.env`.
- Note any platform assumptions at the top. `bootstrap.sh` targets the
  Debian / Raspberry Pi control plane specifically; `setup-homelab-repo.sh` is
  cross-platform (macOS, Linux, Windows via WSL) and is meant to run from a
  developer laptop as well as the Pi.

# Scripts

Dev-time and operational utilities that support `homelab-registry-mcp` but are not
part of the running server. Application code lives in `src/registry_mcp/`; these
scripts are tools for the operator/developer.

For a step-by-step walkthrough of running `install.sh`/`bootstrap.sh` — including
exactly what gets installed and what you'll be prompted for — see
[docs/SETUP.md](../docs/SETUP.md). If you're *changing* either script, see
[Testing changes to install.sh/bootstrap.sh](#testing-changes-to-installshbootstrapsh)
below instead.

## Modular phase scripts

`install.sh` and `bootstrap.sh` are thin orchestrators — arg parsing, prompts for
"what am I about to do", and calling things in order. The actual work each
numbered `[STEP N]` / `[PHASE N]` does lives in its own self-contained script
under `scripts/phases/`:

```
scripts/
├── lib/
│   └── common.sh              # shared helpers: prompt/set_env/state_*/detect_*
├── phases/
│   ├── install/                # install.sh's 9 steps, one file each
│   │   ├── 00-prereqs.sh
│   │   ├── 01-clone.sh
│   │   ├── 02-os-provision.sh
│   │   ├── 03-configure.sh
│   │   ├── 04-homelab-repo.sh
│   │   ├── 05-ansible-inventory.sh
│   │   ├── 06-write-env.sh
│   │   ├── 07-start-server.sh
│   │   └── 08-network.sh
│   └── bootstrap/               # bootstrap.sh's 6 phases, one file each
│       ├── 01-hostname.sh
│       ├── 02-packages.sh
│       ├── 03-ssh-key.sh
│       ├── 04-validation.sh
│       ├── 05-hardware-fingerprint.sh
│       └── 06-static-ip.sh
├── install.sh
└── bootstrap.sh
```

Every phase file is independently runnable, not just a step the orchestrator
happens to call — this is what makes debugging and brownfield/greenfield reruns
practical without re-driving the whole installer:

```bash
# Re-write .env after tweaking a Git token, without repeating Steps 0-5:
bash scripts/phases/install/06-write-env.sh

# Re-apply the static IP after an nmcli failure, without touching packages/SSH/hostname:
sudo -v && bash scripts/phases/bootstrap/06-static-ip.sh

# Re-run just the hardware-facts snapshot:
bash scripts/phases/bootstrap/05-hardware-fingerprint.sh
```

Each phase resolves what it needs the same way install.sh/bootstrap.sh already
document for every prompt: a real environment variable of the same name always
wins (so `INSTALL_DIR=... bash scripts/phases/install/0X-*.sh` and every other
documented pre-seeding trick still works one file at a time), falling back to a
small state file another phase in the same run — or an earlier standalone
invocation — left behind (`~/.homelab-registry-mcp/install-state.env` for
`install.sh`'s steps; `ansible/archive/outputs/.bootstrap-network-state` for
`bootstrap.sh`'s network answers, unchanged from before this split), and only
then to auto-detection or an interactive prompt. `install.sh` clears its state
file at the start of every orchestrated run (and again on exit) so a prior
run's answers — or an option you declined this time — can never leak into the
next one; a human stepping through phases by hand relies on that same file
persisting between invocations, so standalone runs never clear it themselves.

Changing what one step does means editing exactly one file in `scripts/phases/`
— `install.sh`/`bootstrap.sh` themselves should rarely need to change at all.

## What's here

- **`install.sh`** — the recommended one-shot entry point for a fresh control-plane
  node. An orchestrator over its own numbered steps under
  [`scripts/phases/install/`](#modular-phase-scripts) — see that section for how
  to run one step standalone. Sparse-clones root-level files (`docker-compose.yml`,
  `.env.example`, etc.) plus `scripts/`, skipping `src/`, `ansible/`, `tests/`, and
  other build/CI-time directories (the app runs from the GHCR image, not a source
  checkout), runs `bootstrap.sh --skip-network`,
  prompts for the Git secrets and an optional DSPy opt-in, writes `.env`,
  brings the MCP server up with `docker compose up -d`, and only then applies
  the static IP (`bootstrap.sh --network-only`) so the server is already
  running when the SSH session drops. Designed to be run via
  `curl -fsSL <raw-url>/scripts/install.sh | bash`; every prompt can be pre-seeded
  with an environment variable of the same name for non-interactive use.
  `VERSION` (as used in that `curl` URL) also controls which ref
  `install.sh`'s own internal clone checks out — pointing both at the same
  branch/tag, not just the top-level `install.sh` you initially fetched.
  Assumes a greenfield setup — it deliberately doesn't ask about Traefik or
  Authentik discovery, since a fresh homelab won't have those yet. Connect
  the read-only discovery integrations later via the
  `discovery_connect_traefik` / `discovery_connect_authentik` MCP tools.
  `docker-compose.yml` runs only `homelab-registry-mcp` — Komodo and Traefik
  (formerly ADR-006's Pi non-MCP services) are no longer bundled here; deploy
  them as ordinary `nodes/<node>/<service>/compose.yaml` entries in your
  private homelab repo instead, through the same GitOps pipeline described in
  [`ansible/README.md`](../ansible/README.md) — see
  [docs/ARDs/ADR-007-Komodo-Traefik-Move-To-GitOps.md](../docs/ARDs/ADR-007-Komodo-Traefik-Move-To-GitOps.md)
  for why they were removed from this repo's compose file.
  - **Homelab config repo prompt**: folded in from `setup-homelab-repo.sh`
    below. `gh`/`git-crypt` missing from `PATH` skips this prompt with
    instructions rather than blocking the rest of the install (bootstrap.sh
    should have installed both). If `gh` isn't authenticated yet, install.sh
    offers to run the one-time `gh auth login` device-code flow right there
    (declining, or the login not completing, skips the same way) instead of
    requiring a separate manual step beforehand. Reuses the `owner/name`
    already given to the Git provider prompt above when it was answered
    `github`, instead of asking for a repo name twice — see that script's own
    entry for the rest of the details, which apply identically here.
  - **Ansible inventory / hardware onboarding prompt**: folded in from
    `setup-ansible-inventory.sh` below. Only offered when a homelab config
    repo now exists at `SECRETS_REPO_PATH` — either just created by the
    prompt above, or one you already had. No repo at all skips this prompt
    and prints how to run it later. When accepted, this node self-onboards
    into the Ansible inventory the same way any other host would — see that
    script's own entry for the details, which apply identically here.
- **`bootstrap.sh`** — prepares a fresh node for the homelab control plane:
  installs Docker, Ansible, `uv`, `git-crypt`, and the GitHub CLI, sets the
  hostname, generates an SSH key, records a hardware-facts snapshot, and applies
  a static IP. Also an orchestrator over its own numbered phases under
  [`scripts/phases/bootstrap/`](#modular-phase-scripts) — see that section for
  how to run one phase standalone (e.g. re-applying just the static IP after an
  `nmcli` failure). Supports Debian and Ubuntu (ADR-001 §3.1) on any hardware —
  Raspberry Pi or an x86_64/ARM64 VM — detecting the OS, Docker apt repo, network
  interface, and hardware type at runtime rather than assuming a Pi. Run directly
  for a bare provisioning pass, or let `install.sh` drive it —
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
  Also folded inline into `install.sh`'s own homelab-config-repo prompt above
  (Pi defaults there instead — `/opt/homelab` and
  `/opt/homelab/.git-crypt.key`); this script remains the way to set one up
  standalone, or on a non-Pi/cross-platform machine.
- **`setup-ansible-inventory.sh`** — bootstraps (or extends) `ansible.cfg` +
  `ansible/inventory.yml` inside your homelab config repo (Phase 9b): both the
  reusable `.github/workflows/deploy.yml` and the `hardware-discover-now` MCP
  tool expect these to already exist and neither Ansible nor this project
  generates them for you. Seeds the inventory with the control-plane node
  itself (auto-detecting hostname/IP; connects over SSH to its own LAN IP
  like any other host, not `ansible_connection: local` — that would run
  inside the registry-mcp container and gather its ephemeral hostname/OS
  instead of the physical machine's), then interactively prompts for more
  hosts (blank name to stop). Also prompts for the SSH private key Ansible
  should use and runs `ssh-copy-id` against every host you add, including
  the control-plane's own entry — `ssh-keygen` only creates the key pair
  locally, nothing else authorizes it on a target — falling back to
  printing the manual command if that fails or the key's `.pub` file is
  missing. Commits and pushes when done — a push failure warns rather than
  aborting, since the local commit is all `hardware-discover-now` actually
  needs. Idempotent: safe to re-run any time you want to add hosts; skips
  any host already present by name and leaves an existing `ansible.cfg`
  untouched. Run from the control-plane node standalone
  (`scripts/setup-ansible-inventory.sh`), or via `install.sh`'s own Ansible
  inventory prompt above, which runs this exact logic inline once a homelab
  config repo exists — this script stays useful on its own for adding hosts
  later, after the initial install.

## Testing changes to install.sh/bootstrap.sh

Two loops, each catching a different class of bug — see
[CLAUDE.md's "Installer validation (two-tier)" section](../CLAUDE.md#installer-validation-two-tier)
for the full rationale. Push your branch first; both loops clone from GitHub,
not your local working tree.

- **Fast — `gh workflow run install-validation.yml --ref your-branch-name`**
  ([`.github/workflows/install-validation.yml`](../.github/workflows/install-validation.yml)).
  Runs non-interactively on a hosted runner in a few minutes; also runs
  automatically on any PR touching `scripts/**`. Catches logic bugs, env-var
  plumbing issues, and container-health regressions.
- **Slow — [`vagrant/slow-loop/`](../vagrant/slow-loop/README.md)** (Vagrant + libvirt, Debian
  trixie64). Real systemd, real network-interface ownership, and the only
  place the static-IP step actually runs — the fast loop always skips it.
  Manual and interactive; reach for it when a change needs that fidelity.

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

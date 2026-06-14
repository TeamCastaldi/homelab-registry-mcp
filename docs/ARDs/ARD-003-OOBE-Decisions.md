# ADR-003: Out-of-Box Experience (OOBE) Design

**Status:** In Progress  
**Date:** 2026-06-08  
**Deciders:** Nathan Castaldi  
**Location:** `docs/adr/ADR-003-oobe.md`

---

## Context

`homelab-registry-mcp` is designed to be adopted by other homelab operators, not
just used personally. For that to be realistic, a new operator needs a guided
setup experience that:

- Captures the minimum required inputs to make the server functional
- Sets up the deployment infrastructure (GitHub repo, runner, secrets) correctly
  the first time
- Does not require the operator to read the entire codebase before getting started
- Produces a consistent, reproducible environment that Ansible and the MCP can
  both reason about

This document captures the design decisions made during the initial OOBE
implementation, including what inputs are collected, what infrastructure is
configured, and what is deferred to later phases.

The OOBE is not a one-time wizard. It is the authoritative source of node and
environment configuration for the registry. Re-running it should be safe and
idempotent.

---

## Decision

The OOBE is implemented as a guided CLI flow that runs on the control plane node
(Watchtower or equivalent) during initial setup. It collects operator inputs,
writes them to the registry config, and invokes Ansible roles to configure
infrastructure.

The OOBE is divided into phases that mirror the broader project phases, so it
can be extended incrementally without breaking existing setups.

---

## Inputs Collected

### v0.1 — Minimum viable setup (current)

These are the inputs validated during the first real-world OOBE proving run
(2026-06-08, Panoptichron deployment of ConvertX).

| Input | Description | Example |
|---|---|---|
| `GITHUB_USER` | GitHub username (personal account) | `<your-github-username>` |
| `HOMELAB_REPO` | Name of the private homelab config repo | `homelab` |
| `RUNNER_LABEL` | Label assigned to the self-hosted Actions runner | `watchtower` |
| `PROXY_NETWORK` | Name of the Docker proxy network on each host | `proxy-net` |
| `GIT_USER_NAME` | Git commit author name | `Nathan Castaldi` |
| `GIT_USER_EMAIL` | Git commit author email | `<operator-email>` |

**Notes:**
- The homelab config repo lives under the operator's personal GitHub account,
  not an org. `git@github.com:{GITHUB_USER}/{HOMELAB_REPO}.git` is the clone URL.
- `PROXY_NETWORK` is referenced in all compose files as
  `${PROXY_NETWORK:-proxy-net}` with a safe default. The OOBE writes the
  operator's actual value to the node env config.
- `GIT_USER_NAME` and `GIT_USER_EMAIL` must be set on every node that commits
  to the homelab repo (control plane and any worker nodes with a runner).
  These are configured via `git config --global` in the Ansible bootstrap role.

### Deferred to later versions

| Input | Deferred to | Reason |
|---|---|---|
| SMTP provider + credentials | Post v0.1 | Provider not yet validated in production |
| Authentik service account credentials | Phase C | Requires git-crypt to be set up first |
| Traefik dashboard credentials | Phase D | Instance-specific; not in repo |
| Additional node registration | Phase E | Ansible multi-node bootstrap not yet implemented |

---

## Infrastructure Configured by OOBE

### v0.1

| Component | What OOBE does | Where |
|---|---|---|
| GitHub homelab repo | Validates it exists and is reachable via SSH | Control plane |
| git-crypt | Validates unlock succeeds on control plane | Control plane |
| Self-hosted Actions runner | Installs, registers with GitHub, enables as systemd service | Control plane |
| Runner label | Assigned during registration, stored in registry config | Control plane |
| Git global config | Sets `user.name` and `user.email` | Control plane + each node |
| `.gitattributes` | Ensures `**/.env filter=git-crypt diff=git-crypt` is present | Homelab repo |
| Runner → node SSH keypair | Dedicated keypair for runner-to-host deploys; private key stored as `RUNNER_SSH_PRIVATE_KEY` Actions secret | Control plane |
| Deploy workflow | `.github/workflows/deploy.yml` committed to homelab repo | Homelab repo |

### Runner installation (manual until Phase E Ansible role)

The runner is installed on the control plane node under `/opt/actions-runner`.
This location is deliberate — not the default `~/actions-runner` — so it is
owned at the system level and not tied to a user's home directory.

```bash
sudo mkdir -p /opt/actions-runner
sudo chown {USER}:{USER} /opt/actions-runner
cd /opt/actions-runner

# Download ARM64 binary (Raspberry Pi / control plane)
curl -o actions-runner-linux-arm64-{VERSION}.tar.gz -L \
  https://github.com/actions/runner/releases/download/v{VERSION}/actions-runner-linux-arm64-{VERSION}.tar.gz
tar xzf ./actions-runner-linux-arm64-{VERSION}.tar.gz

# Configure with explicit name and label
./config.sh \
  --url https://github.com/{GITHUB_USER}/{HOMELAB_REPO} \
  --token {REGISTRATION_TOKEN} \
  --name {HOSTNAME} \
  --labels {HOSTNAME} \
  --unattended

# Install and start as systemd service
sudo ./svc.sh install {USER}
sudo ./svc.sh start
sudo ./svc.sh status
```

**Architecture note:** The runner binary must match the control plane CPU
architecture. GitHub's default selection is x64. Raspberry Pi requires ARM64.
Select Linux → ARM64 on the GitHub runner registration page.

**Token note:** The registration token expires after 60 minutes. Generate it
immediately before running `config.sh` and complete registration in the same
session.

### Runner → node SSH keypair

The runner SSHes to target hosts to execute deploys. A dedicated keypair is
used — separate from the operator's personal SSH key.

```bash
# On control plane
ssh-keygen -t ed25519 -C "runner@{domain}" -f ~/.ssh/id_ed25519_runner
# No passphrase — required for unattended automation

# Copy public key to each target host
ssh-copy-id -i ~/.ssh/id_ed25519_runner.pub {USER}@{HOST_IP}

# Test
ssh -i ~/.ssh/id_ed25519_runner {USER}@{HOST_IP} "echo success"
```

Store the private key in Vaultwarden as `SSH Key - {hostname} Runner (private)`.
Add it as a GitHub Actions repository secret named `RUNNER_SSH_PRIVATE_KEY`.
The deploy workflow writes it to a temp file, uses it, and deletes it in the
same job step (`if: always()` ensures cleanup on failure too).

### Ansible boundary

The OOBE invokes Ansible for all infrastructure setup. It does not run shell
commands directly. The boundary is:

```
OOBE CLI         →  collects inputs, validates prerequisites
Ansible roles    →  installs runner, configures git, sets up systemd service
Registry config  →  stores operator inputs for use by MCP tools and future runs
```

The runner registration token is short-lived (60 minutes). The OOBE must
complete the runner registration in the same session it generates the token.

---

## Repo Structure Contract

The OOBE establishes and enforces the following directory structure in the
homelab config repo. Deviating from this structure will break MCP discovery
and Ansible targeting.

```
nodes/
  {node}/
    {stack}/
      compose.yaml       ← plain text, committed to repo
      .env               ← git-crypt encrypted, committed to repo
```

**Rules:**
- `{node}` matches the hostname of the Docker host exactly
- `{stack}` matches the service/stack name
- `.env` files are always encrypted via git-crypt — the `.gitattributes` pattern
  `**/.env filter=git-crypt diff=git-crypt` enforces this
- `compose.yaml` never contains hardcoded secrets — all secrets are environment
  variables sourced from `.env`
- `compose.yaml` uses `${PROXY_NETWORK:-proxy-net}` for the proxy network name,
  never a hardcoded string

---

## Deploy Workflow

The deploy workflow lives at `.github/workflows/deploy.yml` in the homelab
config repo. It is the CD layer that connects a Git push to a running service
on a target host.

### Trigger

Only fires on pushes to `main` that include changes under `nodes/`. Pushes that
only touch `.github/`, `docs/`, or other non-node paths do not trigger a deploy.

### Change detection

The `detect-changes` job compares `HEAD~1` to `HEAD` and extracts unique
`{node}/{stack}` pairs from changed file paths. Only stacks with actual file
changes get deployed. A push touching `nodes/panoptichron/convertx/compose.yaml`
deploys only ConvertX on Panoptichron — nothing else.

### Deploy job

Runs on the `watchtower` runner (targeted by label). For each changed stack:

1. Resolves the node name to a host IP via a hardcoded `case` statement
2. Writes the `RUNNER_SSH_PRIVATE_KEY` secret to a temp file
3. SSHes to the target host and runs:
   ```bash
   cd ~/homelab/nodes/{node}/{stack}
   git pull
   docker compose pull
   docker compose up -d
   ```
4. Deletes the temp SSH key file (`if: always()` — runs even on failure)

### Node → IP mapping

Currently hardcoded in the workflow with a `TODO` comment to move to GitHub
Actions variables or registry config during OOBE implementation:

| Node | IP |
|---|---|
| `panoptichron` | `<ip-address>` |
| `heimdall` | `<ip-address>` |
| `waldorf` | `<ip-address>` |

### Actions version policy

Use `actions/checkout@v4.2.2` or later. Node.js 20 actions are deprecated as
of June 2026; Node.js 24 becomes the default on June 16th, 2026.

---

## Port Exposure Policy

During the proving phase (before Traefik is configured on a node), services may
expose ports directly for validation purposes. This is temporary and must be
documented in the compose file:

```yaml
ports:
  - "3000:3000"  # temporary: remove when Traefik is configured on this host
```

Once Traefik is running on the node, all port mappings are removed and traffic
routes through the proxy network. The OOBE will flag any compose files with
temporary port mappings when Traefik is detected on the node.

---

## SSH Key Management Policy

Each node that authenticates to GitHub gets its own SSH keypair. The policy for
managing these keys:

- **Generation:** on the node itself via `ssh-keygen -t ed25519`
- **Storage:** private key saved to Vaultwarden as a secure note named
  `SSH Key - {hostname} (private)`; public key saved as
  `SSH Key - {hostname} (public)`
- **Registration:** public key added to the operator's GitHub account under
  Settings → SSH and GPG keys, titled with the hostname
- **Rotation:** manual, via Vaultwarden → delete old GitHub key → generate new
  keypair → re-register

The Bitwarden SSH agent (desktop app feature) is a future improvement for
workstation SSH management. It does not apply to headless server nodes.

---

## git-crypt Key Management Policy

The git-crypt key is the master secret for all encrypted files in the homelab
repo. Loss of this key means loss of access to all secrets in the repo.

- **Storage:** Vaultwarden secure note, base64-encoded
- **Format:** the key is a binary file — it must be base64-encoded before storing
  as text and decoded before use
- **Usage on a new node:**
  1. Pull base64 string from Vaultwarden
  2. Decode: `echo "BASE64_STRING" | base64 -d > ~/git-crypt-key-binary`
  3. Verify: `file ~/git-crypt-key-binary` should say `data`
  4. Unlock: `git-crypt unlock ~/git-crypt-key-binary`
  5. Delete immediately: `rm ~/git-crypt-key-binary`
- **Key file must never persist on disk** after unlock is complete

---

## Known Issues and Gotchas

These were discovered during the first real-world OOBE run and are documented
here to prevent future operators from hitting the same problems.

| Issue | Cause | Fix |
|---|---|---|
| `.env` committed unencrypted | `.gitattributes` pattern added after first commit | Run `git-crypt status -f` then re-commit |
| `git-crypt unlock` fails with "working directory not clean" | Staged changes present | `git stash`, unlock, `git stash pop` |
| `git-crypt unlock` fails with "unable to read key file" | Key saved as text (copy/paste) not binary | Decode from base64 first |
| base64 decode fails | Trailing stray character in copy/paste (`==c`) | Strip trailing characters, ensure string ends with `==` or `=` or no padding |
| `git clone` returns "Repository not found" | SSH key registered to personal account but clone URL pointed to org | Use `git@github.com:{GITHUB_USER}/{REPO}.git` not org URL |
| Container up but browser can't connect | Port not published to host | Add `ports` mapping; remove when Traefik is configured |
| Runner binary fails with "Exec format error" | x64 binary downloaded for ARM64 host (Raspberry Pi) | Select Linux → ARM64 on GitHub runner registration page |
| `git push` rejected with "fetch first" | Remote has commits not present locally (e.g. committed from another node) | `git pull --rebase` then push |
| `git pull --rebase` leaves working directory path invalid | Rebased while inside a deep subdirectory | `cd` back to repo root and push again |
| Actions warning: Node.js 20 deprecated | `actions/checkout@v4` uses Node.js 20 | Pin to `actions/checkout@v4.2.2` or later |

---

## Version History

| Version | Date | Changes |
|---|---|---|
| v0.1 | 2026-06-08 | Initial design. First proving run: ConvertX deployed to Panoptichron from GitHub homelab repo with git-crypt encrypted secrets. Runner setup deferred. |
| v0.2 | 2026-06-08 | Runner installed on Watchtower (ARM64, systemd, label: `watchtower`). Runner → node SSH keypair established. Deploy workflow committed to homelab repo. First automated deploy succeeded: ConvertX on Panoptichron in 18 seconds. Full CD pipeline proven end-to-end. |

---

## Open Items

| Item | Notes |
|---|---|
| Ansible role for runner registration | Phase E; OOBE invokes this role. Manual steps documented in this ADR until then. |
| OOBE CLI implementation | Not yet implemented; currently a manual process documented here |
| Multi-node git config | `user.name` / `user.email` must be set on every node — bake into Ansible bootstrap role |
| Bitwarden SSH agent | Future improvement for workstation; not applicable to headless nodes |
| PROXY_NETWORK in compose template | Pattern defined; not yet enforced by OOBE validation |
| Traefik on Panoptichron | Deferred; port 3000 exposed directly in the interim |
| Node → IP mapping | Hardcoded in deploy workflow; move to GitHub Actions variables or registry config when OOBE CLI is implemented |
| MCP `ansible` apply mode | Wiring the MCP write path to the proven GitHub Actions deploy pattern — next phase |
| Repo clone path convention | Standardized to `~/homelab` on all nodes. Do not use org-namespaced paths. |
| Komodo migration | Prod services (Heimdall 26, Waldorf 6) still managed by Komodo. Migrate incrementally once first Heimdall service is proven via new pattern. |

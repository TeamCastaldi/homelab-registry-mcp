#!/bin/bash

# ==============================================================================
# HOMELAB REGISTRY MCP — ONE-SHOT INSTALLER
# ==============================================================================
# Curl-bash entry point for a fresh control-plane node. Sparse-clones
# root-level files (docker-compose.yml, .env.example, etc.) plus scripts/,
# skipping src/, ansible/, tests/, and other build/CI-time directories — the
# app runs from the GHCR image, not a source checkout — then hands off to
# bootstrap.sh for OS-level provisioning (Docker/Ansible/uv/git-crypt/gh +
# SSH key), collects the secrets needed for a working .env, brings the MCP
# server up via Docker Compose, and only then applies the static IP
# (bootstrap.sh --network-only) — so the server is already running by the
# time the SSH session drops.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/main/scripts/install.sh | bash
#   # or, from a local clone:
#   bash scripts/install.sh
#
# Every prompt can be pre-seeded via an environment variable of the same name
# (e.g. `INSTALL_DIR=/opt/homelab-registry-mcp GIT_PROVIDER=github bash install.sh`)
# for non-interactive/CI use — any variable already set is not re-prompted.
#
# Assumes a greenfield setup: no Traefik or Authentik yet, so this installer
# doesn't ask about them. Connect those once they exist via the
# discovery_connect_traefik / discovery_connect_authentik MCP tools.
#
# What it does:
#   1. Install git if missing
#   2. Sparse-clone (or update) root-level files + scripts/, skipping src/,
#      ansible/, tests/, and other build/CI-time directories
#   3. Run `bootstrap.sh --skip-network` — Docker, Ansible, uv, git-crypt, gh,
#      SSH key. Deliberately skips the static-IP swap. Every install step
#      skips cleanly if already present, but still fixes up required state
#      (Docker group membership, NetworkManager service) even when it does.
#   4. Prompt for Git/DSPy secrets and opt-in, write .env
#   5. `docker compose up -d` and confirm the server is running
#   6. Run `bootstrap.sh --network-only` — applies the static IP last, unless
#      INSTALL_SKIP_NETWORK=true (CI/test mode — see below), which skips it
# ==============================================================================

set -euo pipefail

# When piped via `curl ... | bash`, stdin is the script itself, not the
# terminal — reopen it from the tty so the prompts below work interactively.
# /dev/tty existing as a device node doesn't mean it's openable: a CI runner
# (or any process with no controlling terminal) has no tty to reopen from —
# `|| true` lets that fail quietly and fall through to whatever stdin already
# is (piped answers, or a prompt() env var skipping the read entirely),
# instead of aborting the whole script under `set -e` before Step 0 even runs.
if [ ! -t 0 ] && [ -e /dev/tty ]; then
    exec < /dev/tty 2>/dev/null || true
fi

REPO_URL="${REPO_URL:-https://github.com/TeamCastaldi/homelab-registry-mcp.git}"
DEFAULT_INSTALL_DIR="${HOME}/homelab-registry-mcp"

# Same variable already used in every documented curl one-liner:
#   export VERSION=main   # or a tag/branch
#   bash -c "$(curl -fsSL .../${VERSION}/scripts/install.sh)"
# Must be `export`ed there, not just assigned — the curl line's own
# ${VERSION} is expanded by the calling shell either way, but only an
# exported VERSION is inherited by the `bash -c` subprocess this script
# itself then runs as. Reused here so the clone below (Step 1) checks out
# the *same* ref, not always main regardless of what VERSION was. Without
# this, VERSION only controlled which install.sh you ran; every file it went
# on to clone — bootstrap.sh, scripts/, monitoring/ — still came from main,
# which silently defeated both release pinning and testing an unmerged
# branch (e.g. the Vagrant slow loop, see vagrant/README.md).
VERSION="${VERSION:-}"

# CI/test-only escape hatch: skips Step 6's static IP application entirely.
# A GitHub Actions runner's own connectivity to the Actions coordinator runs
# over its network interface, so `nmcli connection up` there could sever that
# connection mid-job for a reason unrelated to whether install.sh itself is
# correct. Never set this on a real control-plane node — the MCP server would
# work, but the node would be permanently stuck on DHCP instead of getting
# the static IP a control plane needs (ADR-001 §3.1).
INSTALL_SKIP_NETWORK="${INSTALL_SKIP_NETWORK:-false}"

info()    { echo "  [✓] $*"; }
action()  { echo "  [⚙] $*"; }
warn()    { echo "  [!] $*"; }
header()  { echo ""; echo "$*"; echo "---"; }
die()     { echo ""; echo "ERROR: $*" >&2; exit 1; }

# Prompt for VAR unless it's already set in the environment (non-interactive override).
# Tests whether the variable is *set* (via `+`), not whether it's non-empty (`:-`) —
# an operator pre-seeding an intentionally blank answer (e.g. `GIT_PROVIDER=`) must
# skip the prompt too, not just one seeded with a real value.
prompt() {
    local var_name="$1" prompt_text="$2" default="${3:-}"
    if [ -n "${!var_name+set}" ]; then
        return
    fi
    local input
    if [ -n "$default" ]; then
        read -rp "${prompt_text} [${default}]: " input
        input="${input:-$default}"
    else
        read -rp "${prompt_text}: " input
    fi
    printf -v "$var_name" '%s' "$input"
}

# Same as prompt() but silent (for tokens/keys) and never echoed back.
# Nothing is shown as you type — not even asterisks — so paste/typing
# mistakes are otherwise invisible; print a length-only receipt afterward
# so there's some confirmation without ever putting the value on screen.
prompt_secret() {
    local var_name="$1" prompt_text="$2"
    if [ -n "${!var_name+set}" ]; then
        return
    fi
    local input
    read -rsp "${prompt_text}: " input
    echo ""
    if [ -n "$input" ]; then
        info "Received (${#input} characters, not echoed)"
    else
        warn "No input received — leaving blank"
    fi
    printf -v "$var_name" '%s' "$input"
}

# Set KEY=VALUE in .env, replacing an existing line or appending a new one.
# By default, no-ops when VALUE is empty so unanswered prompts leave the
# .env.example default untouched. Pass allow_empty=true to force-blank a key
# instead (e.g. an optional integration the operator deliberately skipped —
# otherwise its non-empty .env.example placeholder would silently survive).
set_env() {
    local key="$1" value="$2" allow_empty="${3:-false}"
    if [ -z "$value" ] && [ "$allow_empty" != "true" ]; then
        return
    fi
    local escaped
    escaped=$(printf '%s' "$value" | sed -e 's/\\/\\\\/g' -e 's/&/\\&/g' -e 's/|/\\|/g')
    if grep -q "^${key}=" .env; then
        sed -i "s|^${key}=.*|${key}=${escaped}|" .env
    else
        echo "${key}=${escaped}" >> .env
    fi
}

echo ""
echo "================================================"
echo "  HOMELAB REGISTRY MCP — INSTALLER"
echo "================================================"

# =============================================================================
# STEP 0: PREREQUISITES
# =============================================================================

header "[STEP 0] Prerequisites"

if command -v git &>/dev/null; then
    info "git already installed: $(git --version)"
else
    action "Installing git..."
    if [ "${EUID:-$(id -u)}" -eq 0 ]; then
        apt-get update -qq
        apt-get install -y -qq git
    else
        sudo apt-get update -qq
        sudo apt-get install -y -qq git
    fi
    info "git installed: $(git --version)"
fi

# =============================================================================
# STEP 1: CLONE
# =============================================================================

header "[STEP 1] Clone repository"

prompt INSTALL_DIR "Install directory" "$DEFAULT_INSTALL_DIR"

if [ -d "${INSTALL_DIR}/.git" ]; then
    info "Existing checkout found at ${INSTALL_DIR} — pulling latest"
    git -C "$INSTALL_DIR" pull --ff-only
else
    # The control-plane node only ever needs docker-compose.yml, .env.example,
    # and scripts/ — the app itself runs from the GHCR image, not a source
    # checkout (see docker-compose.yml: no build:, no Dockerfile, no bind
    # mount of anything from this repo). A blobless partial clone with
    # cone-mode sparse-checkout gets root-level files (docker-compose.yml,
    # .env.example, etc.) for free and adds just scripts/ — skipping src/,
    # ansible/, tests/, and the rest, which are build/CI-time only.
    action "Cloning ${REPO_URL} into ${INSTALL_DIR} (sparse: root-level files + scripts/ + monitoring/, skipping src/, ansible/, tests/, etc.)..."
    if [ -n "$VERSION" ] && [ "$VERSION" != "main" ]; then
        info "VERSION=${VERSION} — cloning that branch/tag instead of the repo default"
        git clone --filter=blob:none --sparse --branch "$VERSION" "$REPO_URL" "$INSTALL_DIR"
    else
        git clone --filter=blob:none --sparse "$REPO_URL" "$INSTALL_DIR"
    fi
    git -C "$INSTALL_DIR" sparse-checkout set scripts monitoring
    info "Cloned to ${INSTALL_DIR}"
fi

cd "$INSTALL_DIR"

[ -f scripts/bootstrap.sh ] || die "scripts/bootstrap.sh not found in ${INSTALL_DIR} — is this the right repo?"

# =============================================================================
# STEP 2: OS PROVISIONING (Docker/Ansible/uv/git-crypt/gh + SSH key)
# =============================================================================

header "[STEP 2] OS provisioning"
info "Handing off to scripts/bootstrap.sh --skip-network (static IP applied last, in Step 6)"

bash scripts/bootstrap.sh --skip-network

# =============================================================================
# STEP 3: CONFIGURATION
# =============================================================================

header "[STEP 3] Configuration"
echo "These populate .env — press Enter to leave any optional value blank/default."
echo ""
echo "This installer assumes a greenfield setup: no Traefik or Authentik yet,"
echo "so it doesn't ask about them here. Once you stand those up, connect them"
echo "via the discovery_connect_traefik / discovery_connect_authentik MCP tools"
echo "(ask your AI client to run them) — they validate the connection and hand"
echo "back the exact .env lines to add, plus a restart to enable discovery."
echo ""

# github is the common case, so it's the default on a bare Enter; an
# operator who wants no write path at all types "skip" (or "none") rather
# than leaving this blank, since blank now means "accept the default"
# instead of "skip" -- see the pre-seeding comment on prompt() above for the
# non-interactive way to skip (GIT_PROVIDER=).
prompt GIT_PROVIDER "Git provider for the write path (github/gitea, or 'skip')" "github"
if [[ "$GIT_PROVIDER" =~ ^(skip|none)$ ]]; then
    GIT_PROVIDER=""
fi
if [ -n "${GIT_PROVIDER:-}" ]; then
    prompt GIT_REPO "Homelab config repo (owner/name)"
    prompt_secret GIT_TOKEN "Git token (classic: repo scope; fine-grained: Contents + Pull requests, read+write)"
    # The GitHub provider talks to the API root directly (no path prefix
    # added), so the default here must be api.github.com, not github.com —
    # GHES users override with their own /api/v3 root. Gitea/Forgejo has no
    # sensible universal default (self-hosted), so it's prompted with none.
    if [ "$GIT_PROVIDER" == "github" ]; then
        prompt GIT_BASE_URL "Git base URL (blank = public GitHub; GHES: e.g. https://ghe.example.com/api/v3)" "https://api.github.com"
    else
        # No default exists for a self-hosted instance, and leaving this
        # blank would silently disable the write path the operator just
        # asked for (the provider factory requires git_base_url) — keep
        # asking until they give a real host.
        while [ -z "${GIT_BASE_URL:-}" ]; do
            prompt GIT_BASE_URL "Git base URL (your Gitea/Forgejo instance, e.g. https://gitea.example.com — required)"
        done
    fi
fi

# Asks only when DSPY_ENABLED isn't already set at all — pre-seeding
# DSPY_ENABLED=false (not just =true) skips the ask, same as every prompt()
# above; a bare `${DSPY_ENABLED:-false}` default here would make that
# impossible, since it makes the variable "set" before the check runs.
if [ -z "${DSPY_ENABLED+set}" ]; then
    read -rp "Enable Advanced AI Reasoning (DSPy)? [y/N]: " enable_dspy
    if [[ "$enable_dspy" =~ ^[Yy]$ ]]; then
        DSPY_ENABLED=true
        prompt_secret ANTHROPIC_API_KEY "Anthropic API key (used by DSPy)"
    else
        DSPY_ENABLED=false
    fi
fi

echo ""
echo "ADR-005 monitoring/alerting/ingress stack (Beszel, Gatus, Dozzle, WUD,"
echo "Homepage, Glance) — press Enter to skip and bring up only"
echo "homelab-registry-mcp, same as before."
echo ""

COMPOSE_PROFILES="${COMPOSE_PROFILES:-}"
read -rp "Enable the ADR-005 monitoring stack alongside the registry? [y/N]: " enable_monitoring
if [[ "$enable_monitoring" =~ ^[Yy]$ ]]; then
    COMPOSE_PROFILES="monitoring${COMPOSE_PROFILES:+,${COMPOSE_PROFILES}}"
    # Step 2 (bootstrap.sh --skip-network) already asked for this node's
    # static IP and persisted it to NETWORK_STATE_FILE (see bootstrap.sh) --
    # reuse that answer instead of making the operator retype the same
    # address for Homepage's links. Only falls back to asking if the state
    # file is missing (e.g. bootstrap.sh's network prompts were pre-seeded
    # via env vars and skipped, so nothing was ever written) or the operator
    # already pre-seeded CONTROL_PLANE_HOST themselves.
    if [ -z "${CONTROL_PLANE_HOST+set}" ]; then
        NETWORK_STATE_FILE="${INSTALL_DIR}/ansible/archive/outputs/.bootstrap-network-state"
        if [ -f "$NETWORK_STATE_FILE" ]; then
            _saved_ip="$(awk -F= '$1=="SAVED_TARGET_IP" { print $2 }' "$NETWORK_STATE_FILE")"
            if [ -n "$_saved_ip" ]; then
                CONTROL_PLANE_HOST="$_saved_ip"
                info "Using ${CONTROL_PLANE_HOST} (the static IP from Step 2) for Homepage links"
            fi
        fi
    fi
    prompt CONTROL_PLANE_HOST "This node's LAN IP or hostname (for Homepage links)"
    prompt HEALTHCHECKS_PING_URL "Healthchecks.io ping URL (dead man's switch, blank to skip)"
    prompt BESZEL_AGENT_KEY "Beszel hub's agent public key (blank if you haven't set up the hub yet)"
    # beszel-agent has its own profile, not "monitoring" -- it crash-loops
    # without a real key (see its comment in docker-compose.yml), so only
    # start it once one actually exists. Re-run `docker compose up -d` after
    # adding "beszel-agent" to COMPOSE_PROFILES in .env once the hub is set up.
    if [ -n "${BESZEL_AGENT_KEY:-}" ]; then
        COMPOSE_PROFILES="${COMPOSE_PROFILES},beszel-agent"
    fi

    WUD_WEBHOOK_ENABLED=true
    prompt_secret WUD_WEBHOOK_SECRET "WUD webhook shared secret (blank to auto-generate)"
    if [ -z "${WUD_WEBHOOK_SECRET:-}" ]; then
        WUD_WEBHOOK_SECRET="$(openssl rand -hex 32)"
        info "Generated a random WUD webhook secret"
    fi

    read -rp "Also enable cross-node ingress (traefik-kop, requires a Node B already running Traefik+Redis)? [y/N]: " enable_kop
    if [[ "$enable_kop" =~ ^[Yy]$ ]]; then
        COMPOSE_PROFILES="${COMPOSE_PROFILES},cross-node-ingress"
        prompt TRAEFIK_KOP_REDIS_HOST "Node B Redis address (host:port)"
        prompt_secret TRAEFIK_KOP_REDIS_PASSWORD "Node B Redis password"
    fi

    read -rp "Also enable scheduled backups (Autorestic, requires a backup target)? [y/N]: " enable_backup
    if [[ "$enable_backup" =~ ^[Yy]$ ]]; then
        COMPOSE_PROFILES="${COMPOSE_PROFILES},backup"
        prompt AUTORESTIC_BACKUP_TARGET "Autorestic backup target (e.g. s3:bucket, b2:bucket, sftp:host:/path)"
    fi
else
    WUD_WEBHOOK_ENABLED=false
fi

# =============================================================================
# STEP 4: WRITE .env
# =============================================================================

header "[STEP 4] Writing .env"

if [ -f .env ]; then
    warn ".env already exists — leaving it untouched. Edit it by hand if these values changed."
else
    cp .env.example .env
    # allow_empty=true on the optional integrations so leaving a prompt blank
    # actually disables it, instead of silently keeping the .env.example placeholder.
    # TRAEFIK_API_URL / AUTHENTIK_API_URL / AUTHENTIK_TOKEN are deliberately not
    # collected here (greenfield assumption) -- see discovery_connect_traefik /
    # discovery_connect_authentik once those services exist. Blanked explicitly
    # since .env.example ships non-empty placeholder URLs for both, which would
    # otherwise enable discovery against a nonexistent host by default.
    set_env TRAEFIK_API_URL "" true
    set_env AUTHENTIK_API_URL "" true
    set_env AUTHENTIK_TOKEN "" true
    # GIT_PROVIDER is NOT blanked like the three above -- config.py's
    # git_provider is a required Literal["gitea","github","gitlab"], not an
    # Optional[str]. pydantic-settings validates a present-but-empty env var
    # against the literal (it doesn't fall back to the field default for
    # that), so `GIT_PROVIDER=` in .env crashes the server at startup. The
    # write path is actually gated on GIT_BASE_URL/GIT_TOKEN/GIT_REPO being
    # set (see providers/git/build_git_provider) -- GIT_PROVIDER left at
    # .env.example's shipped default is harmless when those three are blank,
    # so skipping the prompt only needs to leave this one untouched.
    set_env GIT_PROVIDER "${GIT_PROVIDER:-}"
    set_env GIT_REPO "${GIT_REPO:-}" true
    set_env GIT_TOKEN "${GIT_TOKEN:-}" true
    set_env GIT_BASE_URL "${GIT_BASE_URL:-}" true
    set_env DSPY_ENABLED "${DSPY_ENABLED}"
    set_env ANTHROPIC_API_KEY "${ANTHROPIC_API_KEY:-}" true
    set_env COMPOSE_PROFILES "${COMPOSE_PROFILES:-}" true
    set_env WUD_WEBHOOK_ENABLED "${WUD_WEBHOOK_ENABLED}"
    set_env WUD_WEBHOOK_SECRET "${WUD_WEBHOOK_SECRET:-}" true
    set_env CONTROL_PLANE_HOST "${CONTROL_PLANE_HOST:-}" true
    set_env HEALTHCHECKS_PING_URL "${HEALTHCHECKS_PING_URL:-}" true
    set_env BESZEL_AGENT_KEY "${BESZEL_AGENT_KEY:-}" true
    set_env TRAEFIK_KOP_REDIS_HOST "${TRAEFIK_KOP_REDIS_HOST:-}" true
    set_env TRAEFIK_KOP_REDIS_PASSWORD "${TRAEFIK_KOP_REDIS_PASSWORD:-}" true
    set_env AUTORESTIC_BACKUP_TARGET "${AUTORESTIC_BACKUP_TARGET:-}" true
    info ".env written"
fi

# =============================================================================
# STEP 5: START THE MCP SERVER
# =============================================================================

header "[STEP 5] Starting the MCP server"

# bootstrap.sh may have just added this user to the docker group in this
# same run — group membership changes don't apply to an already-open shell
# (this script's own process) until logout/login, so a plain `docker`
# command here would fail with "permission denied" on a truly fresh node.
# `sg docker -c "..."` applies the group for just that command, sidestepping
# the need to restart the shell mid-script.
action "docker compose pull && docker compose up -d"
sg docker -c "docker compose pull && docker compose up -d"

action "Waiting for homelab-registry-mcp to report running..."
READY=false
for _ in $(seq 1 30); do
    if sg docker -c "docker compose ps --status running --services" 2>/dev/null | grep -qx "homelab-registry-mcp"; then
        READY=true
        break
    fi
    sleep 2
done

if [ "$READY" == "true" ]; then
    info "homelab-registry-mcp is running"
else
    warn "Could not confirm the container is running — check 'docker compose logs' before continuing"
    read -rp "Continue with the network swap anyway? [y/N]: " force_continue
    [[ "$force_continue" =~ ^[Yy]$ ]] || die "Aborted — server not confirmed up. Re-run 'bash scripts/bootstrap.sh --network-only' manually once it is."
fi

# =============================================================================
# STEP 6: NETWORK  ← LAST — DROPS SSH SESSION
# =============================================================================

header "[STEP 6] Network"

if [ "$INSTALL_SKIP_NETWORK" == "true" ]; then
    warn "INSTALL_SKIP_NETWORK=true — skipping static IP application (CI/test mode)."
else
    echo "The MCP server is up. Applying the static IP now — this is the last step"
    echo "and will drop your SSH session, same as a normal bootstrap.sh run."
    echo ""
    bash scripts/bootstrap.sh --network-only
fi

#!/bin/bash

# ==============================================================================
# HOMELAB REGISTRY MCP — ONE-SHOT INSTALLER
# ==============================================================================
# Curl-bash entry point for a fresh control-plane node. Clones the repo, hands
# off to bootstrap.sh for OS-level provisioning (Docker/Ansible/uv/git-crypt/gh
# + SSH key), collects the secrets needed for a working .env, brings the MCP
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
#   2. Clone (or update) the repository
#   3. Run `bootstrap.sh --skip-network` — Docker, Ansible, uv, git-crypt, gh,
#      SSH key. Deliberately skips the static-IP swap.
#   4. Prompt for Git/DSPy secrets and opt-in, write .env
#   5. `docker compose up -d` and confirm the server is running
#   6. Run `bootstrap.sh --network-only` — applies the static IP last
# ==============================================================================

set -euo pipefail

# When piped via `curl ... | bash`, stdin is the script itself, not the
# terminal — reopen it from the tty so the prompts below work interactively.
if [ ! -t 0 ] && [ -e /dev/tty ]; then
    exec < /dev/tty
fi

REPO_URL="${REPO_URL:-https://github.com/TeamCastaldi/homelab-registry-mcp.git}"
DEFAULT_INSTALL_DIR="${HOME}/homelab-registry-mcp"

info()    { echo "  [✓] $*"; }
action()  { echo "  [⚙] $*"; }
warn()    { echo "  [!] $*"; }
header()  { echo ""; echo "$*"; echo "---"; }
die()     { echo ""; echo "ERROR: $*" >&2; exit 1; }

# Prompt for VAR unless it's already set in the environment (non-interactive override).
prompt() {
    local var_name="$1" prompt_text="$2" default="${3:-}"
    if [ -n "${!var_name:-}" ]; then
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
    if [ -n "${!var_name:-}" ]; then
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
    action "Cloning ${REPO_URL} into ${INSTALL_DIR}..."
    git clone "$REPO_URL" "$INSTALL_DIR"
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

prompt GIT_PROVIDER "Git provider for the write path (github/gitea, blank to skip)"
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

DSPY_ENABLED="${DSPY_ENABLED:-false}"
if [ "$DSPY_ENABLED" != "true" ]; then
    read -rp "Enable Advanced AI Reasoning (DSPy)? [y/N]: " enable_dspy
    if [[ "$enable_dspy" =~ ^[Yy]$ ]]; then
        DSPY_ENABLED=true
        prompt_secret ANTHROPIC_API_KEY "Anthropic API key (used by DSPy)"
    fi
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
    set_env GIT_PROVIDER "${GIT_PROVIDER:-}" true
    set_env GIT_REPO "${GIT_REPO:-}" true
    set_env GIT_TOKEN "${GIT_TOKEN:-}" true
    set_env GIT_BASE_URL "${GIT_BASE_URL:-}" true
    set_env DSPY_ENABLED "${DSPY_ENABLED}"
    set_env ANTHROPIC_API_KEY "${ANTHROPIC_API_KEY:-}" true
    info ".env written"
fi

# =============================================================================
# STEP 5: START THE MCP SERVER
# =============================================================================

header "[STEP 5] Starting the MCP server"

action "docker compose pull && docker compose up -d"
docker compose pull
docker compose up -d

action "Waiting for homelab-registry-mcp to report running..."
READY=false
for _ in $(seq 1 30); do
    if docker compose ps --status running --services 2>/dev/null | grep -qx "homelab-registry-mcp"; then
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
echo "The MCP server is up. Applying the static IP now — this is the last step"
echo "and will drop your SSH session, same as a normal bootstrap.sh run."
echo ""

bash scripts/bootstrap.sh --network-only

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
#   4. Prompt for Git/DSPy/Komodo/Traefik secrets and opt-in
#   5. Optionally create the private homelab config repo (git-crypt-encrypted
#      secrets, Ansible inventory, nodes/ compose files) — requires `gh auth
#      login` to already be done; reuses the Git repo from step 4 if it was
#      given there and points at GitHub
#   6. If that repo exists (from step 5, or one you already had): optionally
#      seed this node into the Ansible inventory hardware-discover-now reads,
#      so hardware onboarding has a real, verified entry for this Pi from
#      the start
#   7. Write .env, `docker compose up -d`, and confirm the server is running
#   8. Run `bootstrap.sh --network-only` — applies the static IP last, unless
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
# on to clone — bootstrap.sh, scripts/ — still came from main,
# which silently defeated both release pinning and testing an unmerged
# branch (e.g. the Vagrant slow loop, see vagrant/slow-loop/README.md).
VERSION="${VERSION:-}"

# CI/test-only escape hatch: skips Step 8's static IP application entirely.
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
    action "Cloning ${REPO_URL} into ${INSTALL_DIR} (sparse: root-level files + scripts/, skipping src/, ansible/, tests/, etc.)..."
    if [ -n "$VERSION" ] && [ "$VERSION" != "main" ]; then
        info "VERSION=${VERSION} — cloning that branch/tag instead of the repo default"
        git clone --filter=blob:none --sparse --branch "$VERSION" "$REPO_URL" "$INSTALL_DIR"
    else
        git clone --filter=blob:none --sparse "$REPO_URL" "$INSTALL_DIR"
    fi
    git -C "$INSTALL_DIR" sparse-checkout set scripts
    info "Cloned to ${INSTALL_DIR}"
fi

cd "$INSTALL_DIR"

[ -f scripts/bootstrap.sh ] || die "scripts/bootstrap.sh not found in ${INSTALL_DIR} — is this the right repo?"

# =============================================================================
# STEP 2: OS PROVISIONING (Docker/Ansible/uv/git-crypt/gh + SSH key)
# =============================================================================

header "[STEP 2] OS provisioning"
info "Handing off to scripts/bootstrap.sh --skip-network (static IP applied last, in Step 8)"

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
# operator who wants no write path at all types "skip" rather than leaving
# this blank, since blank now means "accept the default" instead of "skip"
# -- see the pre-seeding comment on prompt() above for the non-interactive
# way to skip (GIT_PROVIDER=).
prompt GIT_PROVIDER "Git provider for the write path (github/gitea, or 'skip')" "github"
if [ "$GIT_PROVIDER" == "skip" ]; then
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
echo "ADR-006 Pi non-MCP services: Komodo (container management, logs, update"
echo "detection) and Traefik (this node's central ingress). Each is opt-in"
echo "independently — answer N to skip either and bring up only"
echo "homelab-registry-mcp, same as before."
echo ""

COMPOSE_PROFILES="${COMPOSE_PROFILES:-}"
read -rp "Enable Komodo (container management/logs/update detection) on this node? [y/N]: " enable_komodo
read -rp "Enable Traefik as this node's central ingress? [y/N]: " enable_traefik

if [[ "$enable_komodo" =~ ^[Yy]$ ]] || [[ "$enable_traefik" =~ ^[Yy]$ ]]; then
    # Both want this node's LAN IP/hostname (Komodo's OAuth/webhook URL
    # suggestion; useful context for Traefik's own config later). Step 2
    # (bootstrap.sh --skip-network) already asked for this node's static IP
    # and persisted it to NETWORK_STATE_FILE (see bootstrap.sh) — reuse that
    # answer instead of making the operator retype it. Only falls back to
    # asking if the state file is missing (e.g. bootstrap.sh's network
    # prompts were pre-seeded via env vars and skipped, so nothing was ever
    # written) or the operator already pre-seeded CONTROL_PLANE_HOST
    # themselves.
    if [ -z "${CONTROL_PLANE_HOST+set}" ]; then
        NETWORK_STATE_FILE="${INSTALL_DIR}/ansible/archive/outputs/.bootstrap-network-state"
        if [ -f "$NETWORK_STATE_FILE" ]; then
            _saved_ip="$(awk -F= '$1=="SAVED_TARGET_IP" { print $2 }' "$NETWORK_STATE_FILE")"
            if [ -n "$_saved_ip" ]; then
                CONTROL_PLANE_HOST="$_saved_ip"
                info "Using ${CONTROL_PLANE_HOST} (the static IP from Step 2)"
            fi
        fi
    fi
    # Unlike every other prompt in this script, an empty value here isn't a
    # valid "skip" — Komodo/Traefik being enabled means KOMODO_HOST and the
    # traefik-kop instructions need a real host, or they render broken
    # (http://:9120). So this deliberately doesn't use the prompt() helper:
    # prompt() only asks once the var is entirely unset and, on a blank
    # answer, sets it to "" — which counts as "set" on the next check, so a
    # naive `while ...; do prompt ...; done` around it would never actually
    # re-ask. Loop on a raw `read` instead until a non-empty value lands.
    while [ -z "${CONTROL_PLANE_HOST:-}" ]; do
        read -rp "This node's LAN IP or hostname (required for Komodo/Traefik): " CONTROL_PLANE_HOST
    done
fi

if [[ "$enable_komodo" =~ ^[Yy]$ ]]; then
    COMPOSE_PROFILES="komodo${COMPOSE_PROFILES:+,${COMPOSE_PROFILES}}"
    prompt KOMODO_INIT_ADMIN_USERNAME "Komodo admin username" "admin"
    prompt_secret KOMODO_INIT_ADMIN_PASSWORD "Komodo admin password (blank to auto-generate)"
    if [ -z "${KOMODO_INIT_ADMIN_PASSWORD:-}" ]; then
        KOMODO_INIT_ADMIN_PASSWORD="$(openssl rand -hex 16)"
        info "Generated a random Komodo admin password"
    fi
    # Database credentials and the Core<->Periphery/JWT secrets are internal
    # to this compose project (nothing external ever needs to know them),
    # so they're always auto-generated rather than prompted for.
    KOMODO_DATABASE_PASSWORD="$(openssl rand -hex 32)"
    KOMODO_WEBHOOK_SECRET="$(openssl rand -hex 32)"
    KOMODO_JWT_SECRET="$(openssl rand -hex 32)"
    KOMODO_HOST="http://${CONTROL_PLANE_HOST}:9120"
fi

if [[ "$enable_traefik" =~ ^[Yy]$ ]]; then
    COMPOSE_PROFILES="traefik${COMPOSE_PROFILES:+,${COMPOSE_PROFILES}}"
    # Backs the Redis that traefik-kop instances on other nodes publish
    # routing rules to (docker-compose.yml's `traefik-redis` service) —
    # internal to this compose project, always auto-generated.
    TRAEFIK_REDIS_PASSWORD="$(openssl rand -hex 32)"
    info "Generated a random Traefik/Redis password — point each workload"
    info "node's traefik-kop at ${CONTROL_PLANE_HOST}:6379 with it once this is up."
fi

# =============================================================================
# STEP 4: HOMELAB CONFIG REPO
# =============================================================================

header "[STEP 4] Homelab config repo"

echo "This creates the private GitHub repo that holds your homelab's Git-managed"
echo "config: git-crypt-encrypted secrets, the Ansible inventory (next step), and"
echo "the nodes/<node>/<service>/compose.yaml files SOP-001 deploys from. Skip"
echo "this if you already have one, or aren't ready yet — re-run"
echo "scripts/setup-homelab-repo.sh any time later."
echo ""

if ! command -v gh &>/dev/null || ! command -v git-crypt &>/dev/null; then
    warn "gh and/or git-crypt not found — scripts/bootstrap.sh should have installed"
    warn "both. Skipping; run scripts/setup-homelab-repo.sh once they're available."
elif ! gh auth status &>/dev/null; then
    warn "gh is not authenticated — this needs a one-time 'gh auth login' first"
    warn "(device-code flow, safe to run over SSH). Skipping for now; run"
    warn "'gh auth login' then scripts/setup-homelab-repo.sh (or re-run install.sh)."
else
    read -rp "Create/use a private homelab config repo now? [y/N]: " enable_homelab_repo
    if [[ "$enable_homelab_repo" =~ ^[Yy]$ ]]; then
        GITHUB_USER="$(gh api user --jq '.login')"
        if [ "${GIT_PROVIDER:-}" == "github" ] && [ -n "${GIT_REPO:-}" ]; then
            # Reuse the repo already named above rather than asking for the
            # same owner/name twice — gh repo create only ever targets
            # GitHub anyway (this step, like the standalone script it's
            # ported from, has no Gitea/Forgejo equivalent), so a Gitea
            # write path has nothing to reuse here regardless.
            FULL_REPO="$GIT_REPO"
            info "Using ${FULL_REPO} (already given above) as the homelab config repo."
        else
            prompt REPO_NAME "Repo name for your private homelab config repo" "homelab"
            FULL_REPO="${GITHUB_USER}/${REPO_NAME}"
        fi
        prompt SECRETS_REPO_PATH "Where to clone it on this node" "/opt/homelab"
        prompt SECRETS_KEY_PATH "Where to export the git-crypt key" "${SECRETS_REPO_PATH}/.git-crypt.key"

        if gh repo view "$FULL_REPO" &>/dev/null; then
            info "${FULL_REPO} already exists — skipping creation."
        else
            action "Creating private GitHub repo ${FULL_REPO}..."
            gh repo create "$FULL_REPO" --private --description "Homelab configuration (git-crypt encrypted)"
            info "Created."
        fi

        if [ -d "${SECRETS_REPO_PATH}/.git" ]; then
            info "Already cloned at ${SECRETS_REPO_PATH} — skipping clone."
        else
            action "Cloning ${FULL_REPO} -> ${SECRETS_REPO_PATH}..."
            mkdir -p "$(dirname "${SECRETS_REPO_PATH}")"
            gh repo clone "$FULL_REPO" "$SECRETS_REPO_PATH"
        fi

        cd "$SECRETS_REPO_PATH"

        if [ -d .git/git-crypt ]; then
            info "git-crypt already initialised — skipping."
        else
            action "Initialising git-crypt..."
            git-crypt init
        fi

        if [ ! -f .gitattributes ] || ! grep -q "filter=git-crypt" .gitattributes; then
            action "Writing .gitattributes..."
            cat >> .gitattributes <<'EOF'
# Files matching these patterns are encrypted by git-crypt.
# Run: git-crypt unlock <keyfile>  to decrypt after cloning.
**/.env filter=git-crypt diff=git-crypt
EOF
        fi

        # nodes/ skeleton -- WORKLOAD_NODES is env-var-only (not prompted):
        # the Ansible inventory step right after this one already asks for
        # host names interactively, and asking twice for similar-but-not-
        # identical information (bare names here vs name+IP there) would
        # just be confusing. Set it beforehand for non-interactive use if
        # you want scaffolded nodes/<name>/ directories too.
        if [ -n "${WORKLOAD_NODES:-}" ]; then
            action "Creating nodes/ skeleton for: ${WORKLOAD_NODES}..."
            for node in $WORKLOAD_NODES; do
                mkdir -p "nodes/${node}"
                touch "nodes/${node}/.gitkeep"
            done
        else
            mkdir -p nodes
            touch nodes/.gitkeep
        fi

        action "Exporting git-crypt key to ${SECRETS_KEY_PATH}..."
        mkdir -p "$(dirname "${SECRETS_KEY_PATH}")"
        git-crypt export-key "$SECRETS_KEY_PATH"
        chmod 400 "$SECRETS_KEY_PATH"
        info "Key written to ${SECRETS_KEY_PATH} (chmod 400)."
        warn "Back this up to your password manager NOW — it's the only way to"
        warn "decrypt secrets if this node is lost. base64 \"${SECRETS_KEY_PATH}\" | tr -d '\\n'"

        git add .gitattributes nodes/
        if git diff --cached --quiet; then
            info "Nothing new to commit"
        else
            git commit -m "chore: initialise homelab repo with git-crypt"
            # Same reasoning as the Ansible inventory step below: this step
            # is embedded in the middle of install.sh's larger sequence now,
            # not standalone like setup-homelab-repo.sh -- a transient
            # network/auth failure here must not, under set -e, take down
            # the rest of the installer with it. The commit lands locally
            # either way, which is what matters for SECRETS_REPO_PATH below.
            if git push -u origin main 2>/dev/null || git push -u origin HEAD; then
                info "Committed and pushed"
            else
                warn "Committed locally but couldn't push — push manually later:"
                warn "cd ${SECRETS_REPO_PATH} && git push"
            fi
        fi

        cd "$INSTALL_DIR"
        info "Homelab repo ready: https://github.com/${FULL_REPO}"
    fi
fi

# =============================================================================
# STEP 5: ANSIBLE INVENTORY (HARDWARE ONBOARDING)
# =============================================================================

header "[STEP 5] Ansible inventory (hardware onboarding)"

# hardware-discover-now and the reusable CD workflow both read
# ansible.cfg + ansible/inventory.yml from the homelab config repo
# (SECRETS_REPO_PATH) — neither Ansible nor this project ships one for you.
# Step 4 above creates that repo when accepted; if it was skipped (declined,
# or its own preconditions weren't met — no gh auth, etc.), or SECRETS_REPO_PATH
# was pre-seeded to somewhere Step 4 never touched, there's nothing here to
# work with yet. Skip cleanly rather than blocking everything else on a
# prerequisite this step didn't create itself.
ANSIBLE_INVENTORY_REPO="${SECRETS_REPO_PATH:-/opt/homelab}"
if [ ! -d "${ANSIBLE_INVENTORY_REPO}/.git" ]; then
    warn "No homelab config repo found at ${ANSIBLE_INVENTORY_REPO} — skipping."
    warn "Run scripts/setup-homelab-repo.sh, then scripts/setup-ansible-inventory.sh,"
    warn "(or re-run install.sh) to enable hardware onboarding later."
else
    echo "Found a homelab config repo at ${ANSIBLE_INVENTORY_REPO}. Setting this up"
    echo "seeds this node into the Ansible inventory hardware-discover-now reads,"
    echo "so the hardware registry gets a real, verified entry for this Pi."
    echo ""
    read -rp "Set up the Ansible inventory now? [y/N]: " enable_ansible_inventory
    if [[ "$enable_ansible_inventory" =~ ^[Yy]$ ]]; then
        prompt ANSIBLE_SSH_USER "SSH user Ansible should connect as on every host" "$(whoami)"
        prompt SSH_KEY_PATH "Path to the SSH private key Ansible should use" "${HOME}/.ssh/id_ed25519"

        CAN_AUTHORIZE=true
        if [ ! -f "${SSH_KEY_PATH}.pub" ]; then
            warn "${SSH_KEY_PATH}.pub not found — can't auto-authorize this key on new hosts."
            warn "You'll need to run ssh-copy-id yourself for each host added below."
            CAN_AUTHORIZE=false
        fi

        # Authorizes SSH_KEY_PATH on one remote host (ssh-copy-id only copies
        # the *public* key — never touches the private half). Idempotent:
        # ssh-copy-id already skips a key that's authorized there. Never
        # aborts the script — an unreachable host here just means retrying it
        # manually later.
        authorize_host() {
            local ip="$1"
            if [ "$CAN_AUTHORIZE" != "true" ]; then
                return
            fi
            if ssh-copy-id -i "${SSH_KEY_PATH}.pub" -o StrictHostKeyChecking=accept-new \
                "${ANSIBLE_SSH_USER}@${ip}" >/dev/null 2>&1; then
                info "Authorized this key on ${ip}"
            else
                warn "Couldn't authorize the key on ${ip} — run manually: ssh-copy-id -i ${SSH_KEY_PATH}.pub ${ANSIBLE_SSH_USER}@${ip}"
            fi
        }

        cd "$ANSIBLE_INVENTORY_REPO"

        if [ -f ansible.cfg ]; then
            info "ansible.cfg already exists — leaving it as-is"
        else
            action "Writing ansible.cfg..."
            # roles_path is intentionally absent: .github/workflows/deploy.yml
            # sets ANSIBLE_ROLES_PATH itself at invocation time, overriding
            # whatever's here. host_key_checking=False trades a little safety
            # for a CD pipeline that can reach a brand-new host
            # non-interactively — the ad-hoc hardware-discover-now probe
            # already pins StrictHostKeyChecking=accept-new itself regardless
            # of this setting. forks=1 avoids a real ansible-core bug (POSIX
            # fork() of a multithreaded process is undefined behavior — see
            # ansible/ansible#59642): a homelab inventory is small enough
            # that serial execution costs nothing worth trading for it.
            cat > ansible.cfg <<'EOF'
[defaults]
inventory = ansible/inventory.yml
host_key_checking = False
interpreter_python = auto_silent
forks = 1
EOF
            info "Wrote ansible.cfg"
        fi

        mkdir -p ansible
        INVENTORY_FILE="ansible/inventory.yml"
        if [ ! -f "$INVENTORY_FILE" ]; then
            action "Creating ${INVENTORY_FILE}..."
            cat > "$INVENTORY_FILE" <<EOF
all:
  hosts:
  vars:
    ansible_user: ${ANSIBLE_SSH_USER}
EOF
        fi

        # Appends one host under the `  hosts:` key without disturbing the
        # rest of the file — a full YAML merge would need a real parser, so
        # this only works because the file's shape is one this script fully
        # controls (a top-level `all:` with `hosts:`/`vars:` siblings at
        # 2-space indent). Hand-editing the file is fine as long as that
        # shape stays intact.
        add_host() {
            local name="$1" ip="$2"
            if grep -q "^    ${name}:\$" "$INVENTORY_FILE"; then
                warn "${name} is already in the inventory — skipping"
                return
            fi
            awk -v name="$name" -v ip="$ip" '
                { print }
                /^  hosts:$/ && !done { print "    " name ":"; print "      ansible_host: " ip; done=1 }
            ' "$INVENTORY_FILE" > "${INVENTORY_FILE}.tmp"
            mv "${INVENTORY_FILE}.tmp" "$INVENTORY_FILE"
            info "Added ${name} (${ip})"
        }

        # Seed with this node itself, so hardware-discover-now picks up the
        # box running registry-mcp without a manual prompt for it — over SSH
        # to its own LAN IP like any other host, not ansible_connection:
        # local (that would run inside the registry-mcp *container*,
        # gathering its ephemeral hostname/OS instead of the physical
        # machine's).
        CP_HOSTNAME="$(hostname)"
        CP_IP="$(ip route get 8.8.8.8 2>/dev/null | awk '{print $7; exit}' || true)"
        if [ -z "$CP_IP" ]; then
            warn "Couldn't auto-detect this node's IP — enter it manually."
            # Not prompt(): CP_IP is already "set" (to "") by the failed
            # auto-detect above, and prompt() treats set-but-empty as
            # already answered — it would silently skip asking at all, the
            # same bug shape fixed for CONTROL_PLANE_HOST above. Loop on a
            # raw read instead until a non-empty value lands.
            while [ -z "$CP_IP" ]; do
                read -rp "IP address of ${CP_HOSTNAME} (this node): " CP_IP
            done
        fi
        add_host "$CP_HOSTNAME" "$CP_IP"
        authorize_host "$CP_IP"

        echo ""
        echo "Add any other hosts you want in the inventory now (workload nodes,"
        echo "NAS, etc.) — leave the name blank to finish; you can always add more"
        echo "later by re-running scripts/setup-ansible-inventory.sh."
        while true; do
            echo ""
            read -rp "Host name (blank to finish): " HOST_NAME
            [ -z "$HOST_NAME" ] && break
            read -rp "IP address for ${HOST_NAME}: " HOST_IP
            if [ -z "$HOST_IP" ]; then
                warn "No IP given — skipping ${HOST_NAME}"
                continue
            fi
            add_host "$HOST_NAME" "$HOST_IP"
            authorize_host "$HOST_IP"
        done

        git add ansible.cfg ansible/inventory.yml
        if git diff --cached --quiet; then
            info "Nothing new to commit"
        else
            git commit -m "chore: update Ansible inventory"
            # Not a bare `git push`: this step is now embedded in the middle
            # of install.sh's larger sequence, not standalone like
            # setup-ansible-inventory.sh — a transient network/auth failure
            # here must not, under set -e, take down the rest of the
            # installer (starting the server, applying the static IP) along
            # with it. The commit lands locally either way, which is all
            # ANSIBLE_CFG_PATH below actually needs — the push only matters
            # for the separate GitHub Actions deploy workflow reading this
            # same repo, and that's recoverable by hand later.
            if git push; then
                info "Committed and pushed"
            else
                warn "Committed locally but couldn't push — push manually later:"
                warn "cd ${ANSIBLE_INVENTORY_REPO} && git push"
            fi
        fi

        ANSIBLE_CFG_PATH="${ANSIBLE_INVENTORY_REPO}/ansible.cfg"
        cd "$INSTALL_DIR"

        if [ "$CAN_AUTHORIZE" != "true" ]; then
            warn "Some hosts may need ssh-copy-id run manually before hardware-discover-now can reach them."
        fi
    fi
fi

# =============================================================================
# STEP 6: WRITE .env
# =============================================================================

header "[STEP 6] Writing .env"

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
    set_env CONTROL_PLANE_HOST "${CONTROL_PLANE_HOST:-}" true
    set_env KOMODO_INIT_ADMIN_USERNAME "${KOMODO_INIT_ADMIN_USERNAME:-}" true
    set_env KOMODO_INIT_ADMIN_PASSWORD "${KOMODO_INIT_ADMIN_PASSWORD:-}" true
    set_env KOMODO_DATABASE_PASSWORD "${KOMODO_DATABASE_PASSWORD:-}" true
    set_env KOMODO_WEBHOOK_SECRET "${KOMODO_WEBHOOK_SECRET:-}" true
    set_env KOMODO_JWT_SECRET "${KOMODO_JWT_SECRET:-}" true
    set_env KOMODO_HOST "${KOMODO_HOST:-}" true
    set_env TRAEFIK_REDIS_PASSWORD "${TRAEFIK_REDIS_PASSWORD:-}" true
    # Not allow_empty=true: .env.example already ships a real, correct
    # default (/opt/homelab) for this one, unlike the optional-integration
    # fields above -- if Step 4 was skipped, that default must survive
    # untouched, not get force-blanked just because SECRETS_REPO_PATH is
    # unset in this script's own variables.
    set_env SECRETS_REPO_PATH "${SECRETS_REPO_PATH:-}"
    set_env SECRETS_KEY_PATH "${SECRETS_KEY_PATH:-}" true
    # Startup health check prerequisites (Phase 2) — unset unless Step 5 ran
    # and actually set up the inventory. When it did, these are already
    # present for Step 7's first `docker compose up -d` below, so the server
    # starts read-write from the very first boot instead of needing a later
    # restart to pick them up.
    set_env ANSIBLE_CFG_PATH "${ANSIBLE_CFG_PATH:-}" true
    set_env SSH_KEY_PATH "${SSH_KEY_PATH:-}" true
    info ".env written"
fi

# =============================================================================
# STEP 7: START THE MCP SERVER
# =============================================================================

header "[STEP 7] Starting the MCP server"

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
# STEP 8: NETWORK  ← LAST — DROPS SSH SESSION
# =============================================================================

header "[STEP 8] Network"

if [ "$INSTALL_SKIP_NETWORK" == "true" ]; then
    warn "INSTALL_SKIP_NETWORK=true — skipping static IP application (CI/test mode)."
else
    echo "The MCP server is up. Applying the static IP now — this is the last step"
    echo "and will drop your SSH session, same as a normal bootstrap.sh run."
    echo ""
    bash scripts/bootstrap.sh --network-only
fi

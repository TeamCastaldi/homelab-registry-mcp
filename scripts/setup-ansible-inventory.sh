#!/usr/bin/env bash
# setup-ansible-inventory.sh — bootstrap (or extend) the Ansible inventory
# that hardware-discover-now and your own CD deploy workflow both read.
#
# Neither Ansible nor this project ships an inventory for you — the
# reusable `.github/workflows/deploy.yml` and the `hardware-discover-now`
# MCP tool both expect `ansible.cfg` + `ansible/inventory.yml` to already
# exist in your private homelab config repo (SECRETS_REPO_PATH). Run this
# once (from the control-plane node) to seed it with the control-plane
# node itself, then re-run it any time you want to add more hosts.
#
# The OOBE CLI (ADR-003, oobe_setup_ansible) will eventually replace this
# script the same way setup-homelab-repo.sh will be replaced by
# oobe_create_repo/oobe_encrypt_secrets — until then, this is the manual
# path.
#
# Usage:
#   scripts/setup-ansible-inventory.sh
#   SECRETS_REPO_PATH=/opt/homelab scripts/setup-ansible-inventory.sh
#
# What it writes (inside SECRETS_REPO_PATH):
#   ansible.cfg            — minimal defaults, points at ansible/inventory.yml.
#                             Left untouched if it already exists.
#   ansible/inventory.yml  — cone-style inventory. Seeded with the
#                             control-plane node automatically, then you're
#                             prompted for more hosts (blank name to stop).
#                             Re-running skips any host already present.
#
# Commits and pushes ansible.cfg/inventory.yml when it's done (same
# convention as setup-homelab-repo.sh) — nothing else in the repo is
# touched or staged.
#
# After running, add to registry-mcp's .env and recreate the container
# (`docker compose up -d --force-recreate` — a plain restart won't reread
# .env):
#   ANSIBLE_CFG_PATH=<repo>/ansible.cfg
#   SSH_KEY_PATH=<path to the key Ansible should use to reach these hosts>

set -euo pipefail

# When piped via `curl ... | bash`, stdin is the script itself, not the
# terminal — reopen it from the tty so the prompts below work interactively.
if [ ! -t 0 ] && [ -e /dev/tty ]; then
    exec < /dev/tty
fi

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

header "[1/4] Locate your homelab config repo"

prompt SECRETS_REPO_PATH "Path to your homelab config repo" "/opt/homelab"
[ -d "${SECRETS_REPO_PATH}/.git" ] || die "${SECRETS_REPO_PATH} is not a git repo — run setup-homelab-repo.sh (or clone your homelab config repo there) first."
cd "${SECRETS_REPO_PATH}"
info "Using ${SECRETS_REPO_PATH}"

prompt ANSIBLE_SSH_USER "SSH user Ansible should connect as on every host" "$(whoami)"

header "[2/4] ansible.cfg"

if [ -f ansible.cfg ]; then
    info "ansible.cfg already exists — leaving it as-is"
else
    action "Writing ansible.cfg..."
    # roles_path is intentionally absent: .github/workflows/deploy.yml sets
    # ANSIBLE_ROLES_PATH itself at invocation time, overriding whatever's
    # here. host_key_checking=False trades a little safety for a CD
    # pipeline that can reach a brand-new host non-interactively — the
    # ad-hoc hardware-discover-now probe already pins
    # StrictHostKeyChecking=accept-new itself regardless of this setting.
    cat > ansible.cfg <<'EOF'
[defaults]
inventory = ansible/inventory.yml
host_key_checking = False
interpreter_python = auto_silent
EOF
    info "Wrote ansible.cfg"
fi

header "[3/4] Inventory"

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

# Appends one host under the `  hosts:` key without disturbing the rest of
# the file — a full YAML merge would need a real parser, so this only
# works because the file's shape is one this script fully controls
# (a top-level `all:` with `hosts:`/`vars:` siblings at 2-space indent).
# Hand-editing the file is fine as long as that shape stays intact.
# $3 (optional) is one extra `key: value` line, used below to mark the
# control-plane's own entry ansible_connection: local — SSH key-based auth
# needs the *public* key manually copied to a target's authorized_keys
# (ssh-keygen only creates the pair locally), and there's no reason to loop
# an SSH connection back to the box Ansible is already running on.
add_host() {
    local name="$1" ip="$2" extra="${3:-}"
    if grep -q "^    ${name}:\$" "$INVENTORY_FILE"; then
        warn "${name} is already in the inventory — skipping"
        return
    fi
    awk -v name="$name" -v ip="$ip" -v extra="$extra" '
        { print }
        /^  hosts:$/ && !done {
            print "    " name ":"
            print "      ansible_host: " ip
            if (extra != "") print "      " extra
            done=1
        }
    ' "$INVENTORY_FILE" > "${INVENTORY_FILE}.tmp"
    mv "${INVENTORY_FILE}.tmp" "$INVENTORY_FILE"
    info "Added ${name} (${ip})"
}

# Seed with the control-plane node itself, so hardware-discover-now picks up
# the box running registry-mcp without a manual prompt for it. Local, not
# SSH — see add_host's comment above.
CP_HOSTNAME="$(hostname)"
CP_IP="$(ip route get 8.8.8.8 2>/dev/null | awk '{print $7; exit}' || true)"
if [ -z "$CP_IP" ]; then
    warn "Couldn't auto-detect this node's IP — enter it manually."
    prompt CP_IP "IP address of ${CP_HOSTNAME} (this node)"
fi
add_host "$CP_HOSTNAME" "$CP_IP" "ansible_connection: local"

echo ""
echo "Now add any other hosts you want in the inventory (workload nodes, NAS, etc.)."
echo "Leave the name blank when you're done."
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
done

header "[4/4] Commit and push"

git add ansible.cfg ansible/inventory.yml
if git diff --cached --quiet; then
    info "Nothing new to commit"
else
    git commit -m "chore: update Ansible inventory"
    git push
    info "Committed and pushed"
fi

echo ""
echo "============================================================"
echo "  Inventory ready: ${SECRETS_REPO_PATH}/${INVENTORY_FILE}"
echo "============================================================"
echo ""
echo "Before hardware-discover-now can reach any host you just added (not the"
echo "control-plane node itself — that one runs locally, no SSH needed),"
echo "authorize its SSH key on each one:"
echo ""
echo "  ssh-copy-id -i <SSH_KEY_PATH>.pub ${ANSIBLE_SSH_USER}@<host-ip>"
echo ""
echo "ssh-keygen only creates the key pair locally — nothing copies the"
echo "public half to a target's authorized_keys for you."
echo ""
echo "Add these to registry-mcp's .env (if not already set), then recreate"
echo "the container — a plain restart won't reread .env:"
echo ""
echo "  ANSIBLE_CFG_PATH=${SECRETS_REPO_PATH}/ansible.cfg"
echo "  SSH_KEY_PATH=<path to the key Ansible should use to reach these hosts>"
echo ""
echo "  docker compose up -d --force-recreate"
echo ""
echo "Then call the hardware-discover-now MCP tool to fact-gather everything"
echo "listed above into the hardware registry."
echo ""

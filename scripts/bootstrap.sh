#!/bin/bash

# ==============================================================================
# HOMELAB CONTROL PLANE BOOTSTRAP
# ==============================================================================
# Prepares a fresh Raspberry Pi (Debian Trixie) as the homelab control plane.
# Run once after imaging. Leaves the node OOBE-ready for the MCP server.
#
# Workflow:
#   Flash SD → Boot → SSH in via wifi DHCP IP → run this script
#   → all packages installed → static IP applied to eth0 → reconnect
#   → start MCP → run oobe_status
#
# Usage:
#   bash scripts/bootstrap.sh [--dry-run]
#
# What it does:
#   1. Collect target static IP (prompted, default 10.0.0.200)
#   2. Set hostname to "watchtower"
#   3. Install Docker, Ansible + ansible-lint, uv, git-crypt, gh CLI
#   4. Generate ED25519 SSH key (skips if one already exists)
#   5. Validate installs
#   6. Apply static IP to eth0 via nmcli  ← drops SSH session
#
# After reconnecting (ssh chester@10.0.0.200):
#   - Start the MCP server
#   - Run oobe_status to begin OOBE steps 1-15 (ADR-001 §5.1)
#
# ==============================================================================

set -euo pipefail

VERSION="4.2.0"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- FIXED CONFIGURATION ---

HOSTNAME="watchtower"
STATIC_IFACE="eth0"
GATEWAY="10.0.0.2"
DNS_PRIMARY="10.0.0.2"
DNS_SECONDARY="8.8.8.8"
DEFAULT_IP="10.0.0.200"
NM_CON_NAME="static-eth0"

# --- HELPERS ---

info()    { echo "  [✓] $*"; }
action()  { echo "  [⚙] $*"; }
warn()    { echo "  [!] $*"; }
header()  { echo ""; echo "$*"; echo "---"; }
die()     { echo ""; echo "ERROR: $*" >&2; exit 1; }

require_root_or_sudo() {
    if ! sudo -n true 2>/dev/null; then
        die "This script requires sudo. Run as a user with sudo access."
    fi
}

# --- ARGUMENT PARSING ---

DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --help|-h)
            echo "Usage: bash scripts/bootstrap.sh [--dry-run]"
            echo ""
            echo "  --dry-run   Show what would be done without making changes"
            exit 0
            ;;
        *) die "Unknown option: $arg" ;;
    esac
done

# --- ENTRY POINT ---

trap 'echo ""; echo "ERROR: Bootstrap failed at line $LINENO." >&2' ERR

echo ""
echo "================================================"
echo "  HOMELAB CONTROL PLANE BOOTSTRAP  v${VERSION}"
echo "  $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
echo "================================================"
echo ""

require_root_or_sudo

# --- COLLECT STATIC IP UPFRONT ---

echo "This script will:"
echo "  - Set hostname to \"${HOSTNAME}\""
echo "  - Install Docker, Ansible, uv, git-crypt, gh"
echo "  - Generate an ED25519 SSH key (if none exists)"
echo "  - Apply a static IP to ${STATIC_IFACE}  ← last step, drops this SSH session"
echo ""
echo "You are currently connected via: $(ip route get 8.8.8.8 2>/dev/null | awk '{print $7; exit}' || echo 'unknown')"
echo ""

read -rp "Enter static IP for ${STATIC_IFACE} [${DEFAULT_IP}]: " TARGET_IP
TARGET_IP="${TARGET_IP:-${DEFAULT_IP}}"

# Validate IP format
if ! [[ "$TARGET_IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    die "Invalid IP address: $TARGET_IP"
fi

echo ""
echo "Configuration:"
echo "  Hostname:      ${HOSTNAME}"
echo "  Interface:     ${STATIC_IFACE}"
echo "  Static IP:     ${TARGET_IP}/24"
echo "  Gateway:       ${GATEWAY}"
echo "  DNS:           ${DNS_PRIMARY}, ${DNS_SECONDARY}"
echo ""

if [ "$DRY_RUN" == "true" ]; then
    echo "[DRY-RUN] Would perform the following — no changes made:"
    echo ""
    echo "  1. Set hostname to: ${HOSTNAME}"
    echo "  2. Install packages:"
    echo "       docker-ce, docker-ce-cli, containerd.io, docker-compose-plugin"
    echo "       ansible, ansible-lint"
    echo "       uv  (via astral.sh installer)"
    echo "       git-crypt"
    echo "       gh  (GitHub CLI)"
    echo "  3. Generate ED25519 SSH key (if ~/.ssh/id_ed25519 does not exist)"
    echo "  4. Validate all installs"
    echo "  5. Apply static IP ${TARGET_IP}/24 to ${STATIC_IFACE} via nmcli"
    echo "       Gateway: ${GATEWAY}"
    echo "       DNS:     ${DNS_PRIMARY}, ${DNS_SECONDARY}"
    echo "       This will drop your current SSH session."
    echo "       Reconnect: ssh chester@${TARGET_IP}"
    echo ""
    echo "Re-run without --dry-run to execute."
    exit 0
fi

read -rp "Proceed? [y/N]: " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# =============================================================================
# PHASE 1: HOSTNAME
# =============================================================================

header "[PHASE 1] Hostname"

CURRENT_HOSTNAME=$(hostname)
if [ "$CURRENT_HOSTNAME" == "$HOSTNAME" ]; then
    info "Hostname already set to: ${HOSTNAME}"
else
    action "Setting hostname to: ${HOSTNAME}"
    sudo hostnamectl set-hostname "$HOSTNAME"
    # Update /etc/hosts
    if grep -q "127.0.1.1" /etc/hosts; then
        sudo sed -i "s/127.0.1.1.*/127.0.1.1\t${HOSTNAME}/" /etc/hosts
    else
        echo -e "127.0.1.1\t${HOSTNAME}" | sudo tee -a /etc/hosts > /dev/null
    fi
    info "Hostname set to: ${HOSTNAME}"
fi

# =============================================================================
# PHASE 2: PACKAGE INSTALLATION
# =============================================================================

header "[PHASE 2] Package Installation"

# Base update
action "Updating package lists..."
sudo apt-get update -qq

action "Installing prerequisites..."
sudo apt-get install -y -qq ca-certificates curl gnupg lsb-release

# --- DOCKER ---

if command -v docker &>/dev/null; then
    info "Docker already installed: $(docker --version)"
else
    action "Installing Docker (using Bookworm repo — Trixie workaround)..."

    sudo rm -f /etc/apt/sources.list.d/docker.list
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg | \
        sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian bookworm stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update -qq
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

    sudo usermod -aG docker "$USER"
    info "Docker installed: $(docker --version)"
    warn "Docker group added — run 'newgrp docker' or log out/in before using 'docker ps'"
fi

# --- ANSIBLE ---

if command -v ansible &>/dev/null; then
    info "Ansible already installed: $(ansible --version | head -n1)"
else
    action "Installing Ansible + ansible-lint..."
    sudo apt-get install -y -qq ansible ansible-lint
    info "Ansible installed: $(ansible --version | head -n1)"
fi

if ! command -v ansible-lint &>/dev/null; then
    action "Installing ansible-lint..."
    sudo apt-get install -y -qq ansible-lint
fi
info "ansible-lint: $(ansible-lint --version 2>/dev/null | head -n1 || echo 'installed')"

# --- UV ---

if command -v uv &>/dev/null; then
    info "uv already installed: $(uv --version)"
else
    action "Installing uv (Python package manager for registry-mcp)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    info "uv installed: $(uv --version 2>/dev/null || echo 'installed — reload shell to activate')"
fi

# --- GIT-CRYPT ---

if command -v git-crypt &>/dev/null; then
    info "git-crypt already installed: $(git-crypt --version 2>/dev/null || echo 'installed')"
else
    action "Installing git-crypt (Phase C prerequisite)..."
    sudo apt-get install -y -qq git-crypt
    info "git-crypt installed: $(git-crypt --version 2>/dev/null || echo 'installed')"
fi

# --- GITHUB CLI ---

if command -v gh &>/dev/null; then
    info "gh already installed: $(gh --version | head -n1)"
else
    action "Installing GitHub CLI (gh)..."
    sudo apt-get install -y -qq gh
    info "gh installed: $(gh --version | head -n1)"
fi

# --- UTILITY PACKAGES ---

action "Installing utility packages..."
sudo apt-get install -y -qq git vim htop curl wget nfs-common net-tools dnsutils
info "Utility packages installed"

# --- MOUNT POINT STUBS ---

action "Creating NFS mount point stubs..."
sudo mkdir -p /mnt/appdata /mnt/media
info "/mnt/appdata and /mnt/media ready (OOBE step 5 will wire fstab)"

# =============================================================================
# PHASE 3: SSH KEY
# =============================================================================

header "[PHASE 3] SSH Key"

SSH_KEY="$HOME/.ssh/id_ed25519"

if [ -f "$SSH_KEY" ]; then
    info "ED25519 key already exists: ${SSH_KEY}"
else
    action "Generating ED25519 key pair..."
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -C "chester@${HOSTNAME}-$(date +%Y%m%d)"
    info "SSH key generated: ${SSH_KEY}"
fi

echo ""
echo "  Public key (add to GitHub → Settings → SSH Keys if not already done):"
echo "  ---"
cat "${SSH_KEY}.pub"
echo "  ---"

# =============================================================================
# PHASE 4: VALIDATION
# =============================================================================

header "[PHASE 4] Validation"

VALIDATION_PASSED=true

check() {
    local label="$1"
    local cmd="$2"
    if eval "$cmd" &>/dev/null; then
        info "${label}: OK"
    else
        warn "${label}: FAILED"
        VALIDATION_PASSED=false
    fi
}

check "Docker daemon"    "sudo docker info"
check "Docker CLI"       "docker --version"
check "Ansible"          "ansible --version"
check "ansible-lint"     "ansible-lint --version"
check "uv"               "uv --version"
check "git-crypt"        "git-crypt --version"
check "gh CLI"           "gh --version"
check "git"              "git --version"
check "nfs-common"       "dpkg -s nfs-common"
check "/mnt/appdata"     "[ -d /mnt/appdata ]"
check "/mnt/media"       "[ -d /mnt/media ]"
check "SSH key"          "[ -f ${SSH_KEY} ]"
check "Hostname"         "[ \"\$(hostname)\" = \"${HOSTNAME}\" ]"

echo ""
if [ "$VALIDATION_PASSED" == "true" ]; then
    info "All checks passed — node is OOBE-ready"
else
    warn "Some checks failed — review above before proceeding"
    warn "You can still apply the static IP, but fix failures before starting the MCP"
fi

# Save validation log
LOG_DIR="${SCRIPT_DIR}/../ansible/archive/outputs"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/bootstrap-validation-${HOSTNAME}-${TIMESTAMP}.log"
{
    echo "Bootstrap validation — ${HOSTNAME} — $(date -u)"
    echo "Version: ${VERSION}"
    echo "Target IP: ${TARGET_IP}"
    echo ""
    echo "Validation: $([ "$VALIDATION_PASSED" == "true" ] && echo PASSED || echo FAILED)"
} > "$LOG_FILE"
info "Log saved: ${LOG_FILE}"

# =============================================================================
# PHASE 5: HARDWARE FINGERPRINT
# =============================================================================

header "[PHASE 5] Hardware Fingerprint"

FACTS_DIR="${SCRIPT_DIR}/../ansible/archive/outputs"
FACTS_FILE="${FACTS_DIR}/hardware-facts-${HOSTNAME}-${TIMESTAMP}.yml"
mkdir -p "$FACTS_DIR"

cat > "$FACTS_FILE" << YAML
---
# Hardware facts — generated by bootstrap.sh v${VERSION}
# $(date -u +"%Y-%m-%d %H:%M:%S UTC")

hostname: ${HOSTNAME}
role: control-plane
hardware: raspberry-pi
os: $(grep PRETTY_NAME /etc/os-release | cut -d= -f2 | tr -d '"')
arch: $(uname -m)
target_ip: ${TARGET_IP}
gateway: ${GATEWAY}
dns:
  - ${DNS_PRIMARY}
  - ${DNS_SECONDARY}
interface: ${STATIC_IFACE}
ssh_key: ${SSH_KEY}.pub
bootstrapped_at: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
bootstrap_version: ${VERSION}
YAML

info "Hardware facts saved: ${FACTS_FILE}"

# Inventory stub
INVENTORY_DIR="${SCRIPT_DIR}/../ansible/archive/inventory"
mkdir -p "$INVENTORY_DIR"
INVENTORY_FILE="${INVENTORY_DIR}/discovered-hosts.yml"

if ! grep -q "$HOSTNAME" "$INVENTORY_FILE" 2>/dev/null; then
    cat >> "$INVENTORY_FILE" << YAML

# Added by bootstrap.sh — $(date -u +"%Y-%m-%d %H:%M:%S UTC")
# OOBE step 7 (oobe_setup_ansible) will write the full ansible.cfg + inventory
all:
  hosts:
    ${HOSTNAME}:
      ansible_host: ${TARGET_IP}
      ansible_user: chester
      role: control-plane
YAML
    info "Inventory stub written: ${INVENTORY_FILE}"
else
    info "Inventory stub already contains ${HOSTNAME} — skipped"
fi

# =============================================================================
# PHASE 6: STATIC IP  ← LAST — DROPS SSH SESSION
# =============================================================================

header "[PHASE 6] Static IP (eth0)"
echo ""
warn "This is the final step. It will apply the static IP and drop your SSH session."
warn "Reconnect after: ssh chester@${TARGET_IP}"
echo ""
read -rp "  Apply static IP ${TARGET_IP}/24 to ${STATIC_IFACE} now? [y/N]: " apply_ip

if [[ "$apply_ip" =~ ^[Yy]$ ]]; then

    echo ""
    echo "======================================="
    echo "  BOOTSTRAP COMPLETE"
    echo "======================================="
    echo ""
    echo "  Hostname:   ${HOSTNAME}"
    echo "  Static IP:  ${TARGET_IP}/24"
    echo "  Gateway:    ${GATEWAY}"
    echo "  DNS:        ${DNS_PRIMARY}, ${DNS_SECONDARY}"
    echo ""
    echo "  After reconnecting:"
    echo "    ssh chester@${TARGET_IP}"
    echo ""
    echo "  OOBE handoff (ADR-001 §5.1):"
    echo "    Start the MCP server, then run: oobe_status"
    echo "    The OOBE will guide you through steps 1-15."
    echo ""
    echo "  Applying network config in 3 seconds..."
    sleep 3

    # Remove any existing static-eth0 connection
    sudo nmcli connection delete "$NM_CON_NAME" 2>/dev/null || true

    # Create new static connection
    sudo nmcli connection add \
        type ethernet \
        con-name "$NM_CON_NAME" \
        ifname "$STATIC_IFACE" \
        ipv4.method manual \
        ipv4.addresses "${TARGET_IP}/24" \
        ipv4.gateway "$GATEWAY" \
        ipv4.dns "${DNS_PRIMARY},${DNS_SECONDARY}" \
        connection.autoconnect yes

    # Bring it up — this will drop the SSH session
    sudo nmcli connection up "$NM_CON_NAME"

else
    echo ""
    echo "======================================="
    echo "  BOOTSTRAP COMPLETE (network skipped)"
    echo "======================================="
    echo ""
    echo "  Hostname:   ${HOSTNAME}"
    echo "  Network:    not changed — still on DHCP"
    echo ""
    echo "  To apply the static IP later:"
    echo "    sudo nmcli connection add type ethernet con-name ${NM_CON_NAME} \\"
    echo "      ifname ${STATIC_IFACE} ipv4.method manual \\"
    echo "      ipv4.addresses ${TARGET_IP}/24 ipv4.gateway ${GATEWAY} \\"
    echo "      ipv4.dns ${DNS_PRIMARY},${DNS_SECONDARY} connection.autoconnect yes"
    echo "    sudo nmcli connection up ${NM_CON_NAME}"
    echo ""
    echo "  OOBE handoff (ADR-001 §5.1):"
    echo "    Start the MCP server, then run: oobe_status"
    echo "    The OOBE will guide you through steps 1-15."
    echo ""
fi

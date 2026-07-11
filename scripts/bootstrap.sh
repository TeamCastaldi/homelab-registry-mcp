#!/bin/bash

# ==============================================================================
# HOMELAB CONTROL PLANE BOOTSTRAP
# ==============================================================================
# Prepares a fresh control-plane node — Raspberry Pi or any x86_64/ARM64 host
# running Debian or Ubuntu (ADR-001 §3.1) — for homelab-registry-mcp. Run
# once after imaging/installing the OS. Leaves the node OOBE-ready for the
# MCP server.
#
# Workflow:
#   Flash SD (Pi) or install the OS (VM) → boot → SSH in via DHCP IP → run
#   this script → all packages installed → static IP applied to the detected
#   interface → reconnect → start MCP → run oobe_status
#
# Usage:
#   bash scripts/bootstrap.sh [--dry-run] [--skip-network] [--network-only]
#
# What it does:
#   1. Collect target static IP/prefix/gateway/DNS (prompted; defaults are
#      auto-detected from the node's current DHCP lease, so a correct answer
#      usually just means hitting Enter four times)
#   2. Set hostname to "homelab-control-plane"
#   3. Install Docker, Ansible + ansible-lint, uv, git-crypt, gh CLI
#   4. Generate ED25519 SSH key (skips if one already exists)
#   5. Validate installs
#   6. Apply static IP to the detected interface via nmcli  ← drops SSH session
#
# --skip-network runs steps 1-5 only (used by install.sh, which needs Docker
# etc. installed before it brings the MCP server up — the network swap has
# to happen last so the SSH session doesn't drop before that).
# --network-only runs step 6 only, against an already-bootstrapped node.
#
# After reconnecting (ssh $TARGET_USER@192.168.1.200):
#   - Start the MCP server
#   - Run oobe_status to begin OOBE steps 1-15 (ADR-001 §5.1)
#
# ==============================================================================

set -euo pipefail

VERSION="4.3.0"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The user to reconnect/SSH as and to configure in the Ansible inventory.
# Falls back through sudo's caller to $USER — never hardcode an account name.
TARGET_USER="${SUDO_USER:-$USER}"

# --- FIXED CONFIGURATION ---
# Fallback defaults, only used when the node's current network can't be
# auto-detected (see DETECT CURRENT NETWORK below). Every value here is
# prompted for and overridable at runtime.

HOSTNAME="homelab-control-plane"
STATIC_IFACE="eth0"
GATEWAY="192.168.1.1"
DNS_PRIMARY="192.168.1.1"
DNS_SECONDARY="8.8.8.8"
DEFAULT_IP="192.168.1.200"
DEFAULT_PREFIX="24"

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
SKIP_NETWORK=false
NETWORK_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --skip-network) SKIP_NETWORK=true ;;
        --network-only) NETWORK_ONLY=true ;;
        --help|-h)
            echo "Usage: bash scripts/bootstrap.sh [--dry-run] [--skip-network] [--network-only]"
            echo ""
            echo "  --dry-run        Show what would be done without making changes"
            echo "  --skip-network   Run hostname/packages/SSH-key/validation/fingerprint only;"
            echo "                   stop before applying the static IP (Phase 6)"
            echo "  --network-only   Apply the static IP (Phase 6) only, skipping everything else"
            exit 0
            ;;
        *) die "Unknown option: $arg" ;;
    esac
done

if [ "$SKIP_NETWORK" == "true" ] && [ "$NETWORK_ONLY" == "true" ]; then
    die "--skip-network and --network-only are mutually exclusive"
fi

# --- ENTRY POINT ---

trap 'echo ""; echo "ERROR: Bootstrap failed at line $LINENO." >&2' ERR

echo ""
echo "================================================"
echo "  HOMELAB CONTROL PLANE BOOTSTRAP  v${VERSION}"
echo "  $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
echo "================================================"
echo ""

require_root_or_sudo

# --- DETECT OS / DOCKER REPO / INTERFACE / HARDWARE ---
# Supports Debian and Ubuntu (ADR-001 §3.1) on any hardware — Pi or x86_64/
# ARM64 VM. Detected dynamically so a new Debian/Ubuntu release works without
# a script change; anything else fails clearly rather than guessing.

# shellcheck source=/dev/null
. /etc/os-release
case "$ID" in
    debian)
        DOCKER_REPO_OS="debian"
        # Docker has not published a repo for Debian releases newer than
        # bookworm (e.g. trixie) as of this writing — bookworm is ABI-compatible
        # and is the documented workaround. Update this if Docker ships one.
        DOCKER_REPO_CODENAME="bookworm"
        ;;
    ubuntu)
        DOCKER_REPO_OS="ubuntu"
        DOCKER_REPO_CODENAME="$VERSION_CODENAME"
        ;;
    *)
        die "Unsupported OS: ${PRETTY_NAME:-$ID}. This script supports Debian and Ubuntu only (ADR-001 §3.1)."
        ;;
esac

# Default network interface: whatever currently carries the default route.
# Interface names vary a lot across hardware/hypervisors (eth0 on Raspberry Pi
# OS, enp0s3/ens18/etc. on most VMs) — detect it instead of assuming eth0.
# Falls back to the FIXED CONFIGURATION default above if detection fails.
DETECTED_IFACE="$(ip route show default 2>/dev/null | \
    awk '/^default/ { for (i=1; i<=NF; i++) if ($i == "dev") { print $(i+1); exit } }' || true)"
STATIC_IFACE="${DETECTED_IFACE:-$STATIC_IFACE}"
NM_CON_NAME="static-${STATIC_IFACE}"

# --- DETECT CURRENT NETWORK (gateway/prefix/DNS) ---
# The node is still on its DHCP lease at this point, so its current gateway,
# subnet prefix, and DNS servers are real, live values for this network —
# far more reliable than a hardcoded /24 + 192.168.1.1 that silently
# mismatches whatever subnet the operator actually types for TARGET_IP.
# These become the prompt defaults below; nothing here is applied yet.

DETECTED_GATEWAY="$(ip route show default 2>/dev/null | \
    awk '/^default/ { print $3; exit }' || true)"
DETECTED_PREFIX="$(ip -o -f inet addr show dev "$STATIC_IFACE" 2>/dev/null | \
    awk '{print $4}' | cut -d/ -f2 | head -n1 || true)"
# IPv4 only: an IPv6 nameserver here (common with systemd-resolved) would
# sail past this as a detected default, then fail valid_ip_format the moment
# the operator just presses Enter to accept it.
DETECTED_DNS="$(awk '/^nameserver/ && $2 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print $2 }' \
    /etc/resolv.conf 2>/dev/null | paste -sd',' - || true)"

# --- VALIDATION HELPERS ---

valid_ip_format() {
    # Dotted-quad shape check only (not full octet-range validation) —
    # matches the leniency of the pre-existing TARGET_IP check.
    [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

ip_to_int() {
    local a b c d
    IFS=. read -r a b c d <<< "$1"
    echo $(( (a << 24) + (b << 16) + (c << 8) + d ))
}

network_addr() {
    local ip="$1" prefix="$2" mask
    if [ "$prefix" -eq 0 ]; then
        mask=0
    else
        mask=$(( (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF ))
    fi
    echo $(( $(ip_to_int "$ip") & mask ))
}

# Hardware label for the fingerprint YAML — Raspberry Pi's device-tree model
# file is the reliable signal; anything else is reported by architecture.
if [ -f /proc/device-tree/model ] && grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
    HARDWARE_LABEL="raspberry-pi"
else
    HARDWARE_LABEL="$(uname -m)"
fi

# --- COLLECT STATIC IP UPFRONT ---

if [ "$SKIP_NETWORK" == "true" ]; then
    echo "This script will (--skip-network: static IP application deferred):"
    echo "  - Set hostname to \"${HOSTNAME}\""
    echo "  - Install Docker, Ansible, uv, git-crypt, gh"
    echo "  - Generate an ED25519 SSH key (if none exists)"
    echo ""
else
    echo "This script will:"
    echo "  - Set hostname to \"${HOSTNAME}\""
    echo "  - Install Docker, Ansible, uv, git-crypt, gh"
    echo "  - Generate an ED25519 SSH key (if none exists)"
    echo "  - Apply a static IP to ${STATIC_IFACE}  ← last step, drops this SSH session"
    echo ""
    echo "You are currently connected via: $(ip route get 8.8.8.8 2>/dev/null | awk '{print $7; exit}' || echo 'unknown')"
    echo ""
fi

# The network config is always collected (facts/inventory in Phase 5 need it
# whether or not it's applied here) — --skip-network only defers *applying*
# it to Phase 6. Defaults are the node's live DHCP values where detectable,
# so a correct answer is usually just pressing Enter through all four
# prompts — but every value is editable, since the static IP an operator
# picks may land in a different subnet than the current DHCP lease.
read -rp "Enter static IP for ${STATIC_IFACE} [${DEFAULT_IP}]: " TARGET_IP
TARGET_IP="${TARGET_IP:-${DEFAULT_IP}}"
valid_ip_format "$TARGET_IP" || die "Invalid IP address: $TARGET_IP"

read -rp "Subnet prefix length (CIDR bits) [${DETECTED_PREFIX:-$DEFAULT_PREFIX}]: " TARGET_PREFIX
TARGET_PREFIX="${TARGET_PREFIX:-${DETECTED_PREFIX:-$DEFAULT_PREFIX}}"
[[ "$TARGET_PREFIX" =~ ^[0-9]+$ ]] && [ "$TARGET_PREFIX" -ge 1 ] && [ "$TARGET_PREFIX" -le 32 ] || \
    die "Invalid subnet prefix: $TARGET_PREFIX (expected 1-32)"

read -rp "Gateway [${DETECTED_GATEWAY:-$GATEWAY}]: " TARGET_GATEWAY
TARGET_GATEWAY="${TARGET_GATEWAY:-${DETECTED_GATEWAY:-$GATEWAY}}"
valid_ip_format "$TARGET_GATEWAY" || die "Invalid gateway address: $TARGET_GATEWAY"

read -rp "DNS servers, comma-separated [${DETECTED_DNS:-${DNS_PRIMARY},${DNS_SECONDARY}}]: " TARGET_DNS
TARGET_DNS="${TARGET_DNS:-${DETECTED_DNS:-${DNS_PRIMARY},${DNS_SECONDARY}}}"
IFS=',' read -ra _dns_check <<< "$TARGET_DNS"
for _dns_entry in "${_dns_check[@]}"; do
    valid_ip_format "$_dns_entry" || die "Invalid DNS server address: $_dns_entry"
done

# Sanity check: the gateway should live in the same subnet as the chosen
# static IP. This is exactly the kind of mismatch a hardcoded gateway
# produces silently — catch it here instead.
if [ "$(network_addr "$TARGET_IP" "$TARGET_PREFIX")" != "$(network_addr "$TARGET_GATEWAY" "$TARGET_PREFIX")" ]; then
    warn "Gateway ${TARGET_GATEWAY} does not appear to be in ${TARGET_IP}/${TARGET_PREFIX}'s subnet — double-check before proceeding."
fi

echo ""
echo "Configuration:"
echo "  Hostname:      ${HOSTNAME}"
echo "  Interface:     ${STATIC_IFACE}"
echo "  Static IP:     ${TARGET_IP}/${TARGET_PREFIX}"
echo "  Gateway:       ${TARGET_GATEWAY}"
echo "  DNS:           ${TARGET_DNS}"
echo ""

if [ "$DRY_RUN" == "true" ]; then
    echo "[DRY-RUN] Would perform the following — no changes made:"
    echo ""
    if [ "$NETWORK_ONLY" != "true" ]; then
        echo "  1. Set hostname to: ${HOSTNAME}"
        echo "  2. Install packages:"
        echo "       docker-ce, docker-ce-cli, containerd.io, docker-compose-plugin"
        echo "       ansible, ansible-lint"
        echo "       uv  (via astral.sh installer)"
        echo "       git-crypt"
        echo "       gh  (GitHub CLI)"
        echo "  3. Generate ED25519 SSH key (if ~/.ssh/id_ed25519 does not exist)"
        echo "  4. Validate all installs"
    fi
    if [ "$SKIP_NETWORK" != "true" ]; then
        echo "  5. Apply static IP ${TARGET_IP}/${TARGET_PREFIX} to ${STATIC_IFACE} via nmcli"
        echo "       Gateway: ${TARGET_GATEWAY}"
        echo "       DNS:     ${TARGET_DNS}"
        echo "       This will drop your current SSH session."
        echo "       Reconnect: ssh ${TARGET_USER}@${TARGET_IP}"
    fi
    echo ""
    echo "Re-run without --dry-run to execute."
    exit 0
fi

read -rp "Proceed? [y/N]: " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

if [ "$NETWORK_ONLY" != "true" ]; then

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
    action "Installing Docker (repo: ${DOCKER_REPO_OS}/${DOCKER_REPO_CODENAME})..."

    sudo rm -f /etc/apt/sources.list.d/docker.list
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${DOCKER_REPO_OS}/gpg" | \
        sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${DOCKER_REPO_OS} ${DOCKER_REPO_CODENAME} stable" | \
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
    ssh-keygen -t ed25519 -f "$SSH_KEY" -N "" -C "${TARGET_USER}@${HOSTNAME}-$(date +%Y%m%d)"
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

# Joined (not trailing-newline-terminated) so it drops into the heredoc as
# its own line(s) without producing a blank line before the next key.
DNS_YAML_LIST=""
IFS=',' read -ra _dns_facts <<< "$TARGET_DNS"
for _dns_entry in "${_dns_facts[@]}"; do
    if [ -n "$DNS_YAML_LIST" ]; then
        DNS_YAML_LIST="${DNS_YAML_LIST}"$'\n'"  - ${_dns_entry}"
    else
        DNS_YAML_LIST="  - ${_dns_entry}"
    fi
done

cat > "$FACTS_FILE" << YAML
---
# Hardware facts — generated by bootstrap.sh v${VERSION}
# $(date -u +"%Y-%m-%d %H:%M:%S UTC")

hostname: ${HOSTNAME}
role: control-plane
hardware: ${HARDWARE_LABEL}
os: ${PRETTY_NAME}
arch: $(uname -m)
target_ip: ${TARGET_IP}
prefix: ${TARGET_PREFIX}
gateway: ${TARGET_GATEWAY}
dns:
${DNS_YAML_LIST}
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
      ansible_user: ${TARGET_USER}
      role: control-plane
YAML
    info "Inventory stub written: ${INVENTORY_FILE}"
else
    info "Inventory stub already contains ${HOSTNAME} — skipped"
fi

fi # NETWORK_ONLY

if [ "$SKIP_NETWORK" != "true" ]; then

# =============================================================================
# PHASE 6: STATIC IP  ← LAST — DROPS SSH SESSION
# =============================================================================

header "[PHASE 6] Static IP (${STATIC_IFACE})"
echo ""
warn "This is the final step. It will apply the static IP and drop your SSH session."
warn "Reconnect after: ssh ${TARGET_USER}@${TARGET_IP}"
echo ""
read -rp "  Apply static IP ${TARGET_IP}/${TARGET_PREFIX} to ${STATIC_IFACE} now? [y/N]: " apply_ip

if [[ "$apply_ip" =~ ^[Yy]$ ]]; then

    echo ""
    echo "======================================="
    echo "  BOOTSTRAP COMPLETE"
    echo "======================================="
    echo ""
    echo "  Hostname:   ${HOSTNAME}"
    echo "  Static IP:  ${TARGET_IP}/${TARGET_PREFIX}"
    echo "  Gateway:    ${TARGET_GATEWAY}"
    echo "  DNS:        ${TARGET_DNS}"
    echo ""
    echo "  After reconnecting:"
    echo "    ssh ${TARGET_USER}@${TARGET_IP}"
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
        ipv4.addresses "${TARGET_IP}/${TARGET_PREFIX}" \
        ipv4.gateway "$TARGET_GATEWAY" \
        ipv4.dns "${TARGET_DNS}" \
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
    echo "      ipv4.addresses ${TARGET_IP}/${TARGET_PREFIX} ipv4.gateway ${TARGET_GATEWAY} \\"
    echo "      ipv4.dns ${TARGET_DNS} connection.autoconnect yes"
    echo "    sudo nmcli connection up ${NM_CON_NAME}"
    echo ""
    echo "  OOBE handoff (ADR-001 §5.1):"
    echo "    Start the MCP server, then run: oobe_status"
    echo "    The OOBE will guide you through steps 1-15."
    echo ""
fi

fi # SKIP_NETWORK

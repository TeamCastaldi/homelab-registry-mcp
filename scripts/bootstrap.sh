#!/bin/bash

# ==============================================================================
# HOMELAB CONTROL PLANE BOOTSTRAP
# ==============================================================================
# Prepares a fresh control-plane node — Raspberry Pi or any x86_64/ARM64 host
# running Debian or Ubuntu (ADR-001 §3.1) — for homelab-registry-mcp. Run
# once after imaging/installing the OS. Leaves the node OOBE-ready for the
# MCP server.
#
# This script is an orchestrator: arg parsing, OS/network detection, and the
# interactive "what am I about to do" confirmation live here; the actual work
# happens in scripts/phases/bootstrap/*.sh, six self-contained phase scripts
# invoked below in order. Each one is independently runnable too — see its
# own header comment — which is the easiest way to debug or re-apply just
# one phase (e.g. `bash scripts/phases/bootstrap/06-static-ip.sh` after an
# nmcli failure) instead of re-running everything.
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
#   2. Set hostname to "homelab-control-plane"                      [phases/bootstrap/01-hostname.sh]
#   3. Install Docker, Ansible + ansible-lint, uv, git-crypt, gh CLI [phases/bootstrap/02-packages.sh]
#   4. Generate ED25519 SSH key (skips if one already exists)       [phases/bootstrap/03-ssh-key.sh]
#   5. Validate installs                                            [phases/bootstrap/04-validation.sh]
#   6. Record a hardware-facts snapshot + inventory stub             [phases/bootstrap/05-hardware-fingerprint.sh]
#   7. Apply static IP to the detected interface via nmcli  ← drops SSH session
#                                                                     [phases/bootstrap/06-static-ip.sh]
#
# --skip-network runs steps 2-6 only (used by install.sh, which needs Docker
# etc. installed before it brings the MCP server up — the network swap has
# to happen last so the SSH session doesn't drop before that).
# --network-only runs step 7 only, against an already-bootstrapped node,
# silently reusing the static IP/prefix/gateway/DNS collected during the
# --skip-network run instead of asking again.
#
# After reconnecting (ssh $TARGET_USER@192.168.1.200):
#   - Start the MCP server
#   - Run oobe_status to begin OOBE steps 1-15 (ADR-001 §5.1)
#
# ==============================================================================

set -euo pipefail

VERSION="4.3.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PHASES_DIR="${SCRIPT_DIR}/phases/bootstrap"

# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

# install.sh invokes this script twice as separate processes — once with
# --skip-network (where the operator answers the network prompts) and once
# with --network-only (Phase 7, which actually applies them). Each is a
# fresh process with no memory of the other, so without this file the
# second invocation would re-prompt from the hardcoded defaults instead of
# what was just answered. The phase scripts under scripts/phases/bootstrap/
# read/write it too (via collect_network_config in lib/common.sh), which is
# what makes each of them independently re-runnable days later.
NETWORK_STATE_FILE="${SCRIPT_DIR}/../ansible/archive/outputs/.bootstrap-network-state"

# The user to reconnect/SSH as and to configure in the Ansible inventory.
# Falls back through sudo's caller to $USER — never hardcode an account name.
TARGET_USER="${SUDO_USER:-$USER}"

# --- FIXED CONFIGURATION ---
# Fallback defaults, only used when the node's current network can't be
# auto-detected (see collect_network_config in lib/common.sh). Every value
# here is prompted for and overridable at runtime.

HOSTNAME="homelab-control-plane"
GATEWAY="192.168.1.1"
DNS_PRIMARY="192.168.1.1"
DNS_SECONDARY="8.8.8.8"
DEFAULT_IP="192.168.1.200"
DEFAULT_PREFIX="24"

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

# --- DETECT OS / INTERFACE / HARDWARE ---
# Supports Debian and Ubuntu on any hardware — Pi or x86_64/ARM64 VM.
# Detected dynamically so a new Debian/Ubuntu release works without a
# script change; anything else fails clearly rather than guessing.

detect_docker_repo_os
detect_static_iface
detect_hardware_label

if [ "$SKIP_NETWORK" == "true" ]; then
    echo "This script will (--skip-network: static IP application deferred):"
    echo "  - Set hostname to \"${HOSTNAME}\""
    echo "  - Install Docker, Ansible, uv, git-crypt, gh"
    echo "  - Generate an ED25519 SSH key (if none exists)"
    echo ""
elif [ "$NETWORK_ONLY" == "true" ]; then
    echo "This script will (--network-only: hostname/packages/SSH key already done):"
    if is_container; then
        echo "  - Skip static IP application to ${STATIC_IFACE} — running inside a container, where addressing is owned by the host"
    else
        echo "  - Apply a static IP to ${STATIC_IFACE}  ← last step, drops this SSH session"
    fi
    echo ""
    echo "You are currently connected via: $(ip route get 8.8.8.8 2>/dev/null | awk '{print $7; exit}' || echo 'unknown')"
    echo ""
else
    echo "This script will:"
    echo "  - Set hostname to \"${HOSTNAME}\""
    echo "  - Install Docker, Ansible, uv, git-crypt, gh"
    echo "  - Generate an ED25519 SSH key (if none exists)"
    if is_container; then
        echo "  - Skip static IP application to ${STATIC_IFACE} — running inside a container, where addressing is owned by the host"
    else
        echo "  - Apply a static IP to ${STATIC_IFACE}  ← last step, drops this SSH session"
    fi
    echo ""
    echo "You are currently connected via: $(ip route get 8.8.8.8 2>/dev/null | awk '{print $7; exit}' || echo 'unknown')"
    echo ""
fi

# The network config is always collected (facts/inventory in Phase 5 need it
# whether or not it's applied in Phase 6) — --skip-network only defers
# *applying* it. Only a --network-only run reuses a complete prior answer
# silently; every other mode always asks (with live-detected defaults, so a
# correct answer is usually just pressing Enter four times).
NETWORK_REUSE_IF_COMPLETE="$NETWORK_ONLY"
collect_network_config "$NETWORK_STATE_FILE"

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
        if is_container; then
            echo "  5. Skip static IP application — running inside a container, where"
            echo "       addressing is owned by the host, not this guest."
        else
            echo "  5. Apply static IP ${TARGET_IP}/${TARGET_PREFIX} to ${STATIC_IFACE} via nmcli"
            echo "       Gateway: ${TARGET_GATEWAY}"
            echo "       DNS:     ${TARGET_DNS}"
            echo "       This will drop your current SSH session."
            echo "       Reconnect: ssh ${TARGET_USER}@${TARGET_IP}"
        fi
    fi
    echo ""
    echo "Re-run without --dry-run to execute."
    exit 0
fi

read -rp "Proceed? [y/N]: " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

if [ "$NETWORK_ONLY" != "true" ]; then
    bash "${PHASES_DIR}/01-hostname.sh"
    bash "${PHASES_DIR}/02-packages.sh"
    bash "${PHASES_DIR}/03-ssh-key.sh"
    bash "${PHASES_DIR}/04-validation.sh"
    bash "${PHASES_DIR}/05-hardware-fingerprint.sh"
fi

if [ "$SKIP_NETWORK" != "true" ]; then
    bash "${PHASES_DIR}/06-static-ip.sh"
fi

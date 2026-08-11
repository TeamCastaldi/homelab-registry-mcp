#!/bin/bash

# ==============================================================================
# BOOTSTRAP PHASE 6 — STATIC IP  ← LAST — DROPS SSH SESSION
# ==============================================================================
# Applies the collected static IP/prefix/gateway/DNS to the detected
# interface via nmcli. Skips cleanly inside a container (LXC, etc.), where
# addressing is normally owned by the host. This is deliberately the last
# thing bootstrap.sh runs — reconnect afterward at the new address.
#
# Invoked by scripts/bootstrap.sh (equivalent to `bootstrap.sh
# --network-only`). Also fully self-contained — reuses a previously
# collected/saved answer if one exists, otherwise prompts for it:
#   sudo -v && bash scripts/phases/bootstrap/06-static-ip.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

require_root_or_sudo
detect_static_iface

TARGET_USER="${TARGET_USER:-${SUDO_USER:-$USER}}"
NETWORK_STATE_FILE="${REPO_ROOT}/ansible/archive/outputs/.bootstrap-network-state"

header "[PHASE 6] Static IP (${STATIC_IFACE})"

if is_container; then
    info "Running inside a container ($(systemd-detect-virt --container 2>/dev/null || echo 'lxc')) — network addressing is normally owned by the host, not this guest (e.g. a Proxmox LXC gets its IP from the container's net0 config on the host)."
    info "Skipping the in-guest static IP step. If this container should have a different address, set it via the host, not nmcli inside the guest."
    rm -f "$NETWORK_STATE_FILE" 2>/dev/null || true

    echo ""
    echo "======================================="
    echo "  BOOTSTRAP COMPLETE (network owned by host)"
    echo "======================================="
    echo ""
    echo "  Network:    unchanged — set this container's static IP via the host"
    echo "              (e.g. 'pct set <vmid> -net0 ...,ip=<ip>/<prefix>,gw=<gateway>' on Proxmox)"
    echo ""
    echo "  OOBE handoff (ADR-001 §5.1):"
    echo "    Start the MCP server, then run: oobe_status"
    echo "    The OOBE will guide you through steps 1-15."
    echo ""

    exit 0
fi

# Always reuse a complete, already-collected answer (from bootstrap.sh's own
# preamble moments ago, or an earlier standalone phase run) — only prompts
# when there's genuinely nothing to reuse.
NETWORK_REUSE_IF_COMPLETE=true
collect_network_config "$NETWORK_STATE_FILE"

echo ""
echo "Configuration:"
echo "  Interface:     ${STATIC_IFACE}"
echo "  Static IP:     ${TARGET_IP}/${TARGET_PREFIX}"
echo "  Gateway:       ${TARGET_GATEWAY}"
echo "  DNS:           ${TARGET_DNS}"
echo ""

# Fail clearly here rather than mid-way through nmcli — e.g. this node was
# bootstrapped by an older script version before NetworkManager install was
# added, or something else still owns the interface: netplan still
# rendering it via systemd-networkd (Ubuntu's default), or ifupdown via
# /etc/network/interfaces (Debian's default, including Raspberry Pi OS) —
# installing the NetworkManager package alone doesn't hand control of the
# device to it either way.
command -v nmcli &>/dev/null || die "nmcli not found. Install NetworkManager first: sudo apt-get install -y network-manager && sudo systemctl enable --now NetworkManager — then re-run this script."

# `|| true` on the whole pipeline: under set -e/pipefail, a non-zero nmcli
# (e.g. the service isn't actually running) would otherwise abort via a
# generic error instead of the specific die() messages below.
_nm_iface_state="$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: -v d="$STATIC_IFACE" '$1==d {print $2}' || true)"
if [ -z "$_nm_iface_state" ]; then
    die "NetworkManager doesn't see a device named ${STATIC_IFACE} — check 'nmcli device status' and that the service is running ('systemctl status NetworkManager'), then re-run this script."
elif [ "$_nm_iface_state" == "unmanaged" ]; then
    # Detect which mechanism actually owns the interface before prescribing
    # a remedy — netplan/networkd is Ubuntu's default, ifupdown is Debian's
    # (including Raspberry Pi OS, this project's primary hardware target),
    # and the wrong guidance sends the operator chasing a config that isn't
    # there.
    if compgen -G "/etc/netplan/*.yaml" > /dev/null 2>&1; then
        die "NetworkManager is installed but ${STATIC_IFACE} is unmanaged — netplan is likely still rendering it via systemd-networkd. Add 'renderer: NetworkManager' to /etc/netplan/*.yaml, run 'sudo netplan apply', then re-run this script."
    elif grep -qE "^\s*(auto|allow-hotplug)\s+${STATIC_IFACE}\b" /etc/network/interfaces 2>/dev/null; then
        # The common case on Debian/Raspberry Pi OS (this project's primary
        # hardware target, ADR-001 §3.1): the stock NetworkManager.conf ships
        # `managed=false` under [ifupdown] specifically to defer to it. Fixed
        # in place rather than dying — requiring a manual edit + restart +
        # re-run here would break the "one-shot" promise for the majority of
        # real installs, not just an edge case. (The netplan branch above
        # stays manual: a YAML rewrite isn't safe to automate the same way.)
        action "${STATIC_IFACE} is unmanaged because ifupdown owns it — setting managed=true under [ifupdown] in NetworkManager.conf..."
        _nm_conf="/etc/NetworkManager/NetworkManager.conf"
        if sudo grep -q '^\[ifupdown\]' "$_nm_conf" 2>/dev/null; then
            sudo sed -i '/^\[ifupdown\]/,/^\[/{/^[[:space:]]*managed[[:space:]]*=/d}' "$_nm_conf"
            sudo sed -i '/^\[ifupdown\]/a managed=true' "$_nm_conf"
        else
            printf '\n[ifupdown]\nmanaged=true\n' | sudo tee -a "$_nm_conf" > /dev/null
        fi
        sudo systemctl restart NetworkManager
        sleep 2
        _nm_iface_state="$(nmcli -t -f DEVICE,STATE device status 2>/dev/null | awk -F: -v d="$STATIC_IFACE" '$1==d {print $2}' || true)"
        if [ "$_nm_iface_state" == "unmanaged" ]; then
            die "Set managed=true under [ifupdown] in ${_nm_conf} and restarted NetworkManager, but ${STATIC_IFACE} is still unmanaged. Check 'cat ${_nm_conf}' and 'nmcli device show ${STATIC_IFACE}' for what else might be excluding it."
        fi
        info "${STATIC_IFACE} is now managed by NetworkManager"
    else
        die "NetworkManager is installed but ${STATIC_IFACE} is unmanaged, and the cause couldn't be auto-detected (no netplan config, no matching /etc/network/interfaces stanza). Check 'cat /etc/NetworkManager/NetworkManager.conf' and 'nmcli device show ${STATIC_IFACE}' for what's excluding it, then re-run this script."
    fi
fi

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

    NM_CON_NAME="static-${STATIC_IFACE}"

    # Remove any existing static connection for this interface
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

    # Config is being applied now — clear the saved answers so a future,
    # unrelated bootstrap run doesn't inherit them. Done before "up" since
    # that drops the SSH session and nothing after it is guaranteed to run.
    rm -f "$NETWORK_STATE_FILE" 2>/dev/null || true

    # Bring it up — this will drop the SSH session
    sudo nmcli connection up "$NM_CON_NAME"

else
    NM_CON_NAME="static-${STATIC_IFACE}"
    echo ""
    echo "======================================="
    echo "  BOOTSTRAP COMPLETE (network skipped)"
    echo "======================================="
    echo ""
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

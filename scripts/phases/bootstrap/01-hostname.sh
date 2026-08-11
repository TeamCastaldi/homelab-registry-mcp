#!/bin/bash

# ==============================================================================
# BOOTSTRAP PHASE 1 — HOSTNAME
# ==============================================================================
# Sets this node's hostname to the fixed control-plane name. Idempotent —
# safe to re-run any time; a no-op if already set.
#
# Invoked by scripts/bootstrap.sh as part of a normal (non `--network-only`)
# run. Also fully self-contained — run it on its own to debug or re-apply
# just this phase:
#   sudo -v && bash scripts/phases/bootstrap/01-hostname.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

require_root_or_sudo

HOSTNAME="homelab-control-plane"

header "[PHASE 1] Hostname"

CURRENT_HOSTNAME=$(hostname)
if [ "$CURRENT_HOSTNAME" == "$HOSTNAME" ]; then
    info "Hostname already set to: ${HOSTNAME}"
else
    action "Setting hostname to: ${HOSTNAME}"
    sudo hostnamectl set-hostname "$HOSTNAME"
    if grep -q "127.0.1.1" /etc/hosts; then
        sudo sed -i "s/127.0.1.1.*/127.0.1.1\t${HOSTNAME}/" /etc/hosts
    else
        echo -e "127.0.1.1\t${HOSTNAME}" | sudo tee -a /etc/hosts > /dev/null
    fi
    info "Hostname set to: ${HOSTNAME}"
fi

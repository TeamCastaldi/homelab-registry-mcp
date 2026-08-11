#!/bin/bash

# ==============================================================================
# BOOTSTRAP PHASE 3 — SSH KEY
# ==============================================================================
# Generates an ED25519 SSH key at ~/.ssh/id_ed25519 if one doesn't already
# exist, and prints the public key to add to GitHub. Idempotent.
#
# Invoked by scripts/bootstrap.sh as part of a normal (non `--network-only`)
# run. Also fully self-contained:
#   bash scripts/phases/bootstrap/03-ssh-key.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

HOSTNAME="homelab-control-plane"
TARGET_USER="${TARGET_USER:-${SUDO_USER:-$USER}}"

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

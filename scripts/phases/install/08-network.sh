#!/bin/bash

# ==============================================================================
# INSTALL STEP 8 — NETWORK  ← LAST — DROPS SSH SESSION
# ==============================================================================
# Hands off to scripts/bootstrap.sh --network-only, applying the static IP
# collected back in Step 2. Deliberately the last step of install.sh so the
# MCP server is already up and running by the time this drops the SSH
# session.
#
# Invoked by scripts/install.sh. Also fully self-contained:
#   INSTALL_DIR=~/homelab-registry-mcp bash scripts/phases/install/08-network.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"
require_install_dir

# CI/test-only escape hatch: skips the static IP application entirely. A
# GitHub Actions runner's own connectivity to the Actions coordinator runs
# over its network interface, so `nmcli connection up` there could sever
# that connection mid-job for a reason unrelated to whether install.sh
# itself is correct. Never set this on a real control-plane node — the MCP
# server would work, but the node would be permanently stuck on DHCP
# instead of getting the static IP a control plane needs (ADR-001 §3.1).
INSTALL_SKIP_NETWORK="${INSTALL_SKIP_NETWORK:-false}"

header "[STEP 8] Network"

if [ "$INSTALL_SKIP_NETWORK" == "true" ]; then
    warn "INSTALL_SKIP_NETWORK=true — skipping static IP application (CI/test mode)."
else
    echo "The MCP server is up. Applying the static IP now — this is the last step"
    echo "and will drop your SSH session, same as a normal bootstrap.sh run."
    echo ""
    bash scripts/bootstrap.sh --network-only
fi

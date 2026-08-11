#!/bin/bash

# ==============================================================================
# INSTALL STEP 2 — OS PROVISIONING
# ==============================================================================
# Hands off to scripts/bootstrap.sh --skip-network — Docker, Ansible, uv,
# git-crypt, gh, SSH key, hostname. Deliberately skips the static-IP swap
# (that's Step 8, scripts/phases/install/08-network.sh — applied last so the
# server is already running when the SSH session drops).
#
# Invoked by scripts/install.sh. Also fully self-contained (run against an
# existing checkout):
#   INSTALL_DIR=~/homelab-registry-mcp bash scripts/phases/install/02-os-provision.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"
require_install_dir

header "[STEP 2] OS provisioning"
info "Handing off to scripts/bootstrap.sh --skip-network (static IP applied last, in Step 8)"

bash scripts/bootstrap.sh --skip-network

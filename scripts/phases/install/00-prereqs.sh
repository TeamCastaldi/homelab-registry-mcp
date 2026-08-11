#!/bin/bash

# ==============================================================================
# INSTALL STEP 0 — PREREQUISITES
# ==============================================================================
# Installs git if it isn't already present — needed to clone the repo in
# Step 1. Idempotent.
#
# Invoked by scripts/install.sh. Also fully self-contained:
#   bash scripts/phases/install/00-prereqs.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

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

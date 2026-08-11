#!/bin/bash

# ==============================================================================
# BOOTSTRAP PHASE 4 — VALIDATION
# ==============================================================================
# Confirms every package/key Phases 1-3 install actually landed, and saves a
# timestamped log under ansible/archive/outputs/. Read-only — never
# installs or changes anything itself, so it's safe to re-run any time as a
# health check.
#
# Invoked by scripts/bootstrap.sh as part of a normal (non `--network-only`)
# run. Also fully self-contained:
#   bash scripts/phases/bootstrap/04-validation.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

VERSION="4.3.0"
HOSTNAME="homelab-control-plane"
SSH_KEY="$HOME/.ssh/id_ed25519"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)

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

LOG_DIR="${REPO_ROOT}/ansible/archive/outputs"
mkdir -p "$LOG_DIR"
LOG_FILE="${LOG_DIR}/bootstrap-validation-${HOSTNAME}-${TIMESTAMP}.log"
{
    echo "Bootstrap validation — ${HOSTNAME} — $(date -u)"
    echo "Version: ${VERSION}"
    echo ""
    echo "Validation: $([ "$VALIDATION_PASSED" == "true" ] && echo PASSED || echo FAILED)"
} > "$LOG_FILE"
info "Log saved: ${LOG_FILE}"

# Deliberately non-fatal: a failed check here is diagnostic, not a reason to
# abort the rest of bootstrap.sh (Phase 5/6 can still be useful to run, and
# the warning above already tells the operator to fix failures before
# starting the MCP server).

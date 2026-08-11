#!/bin/bash

# ==============================================================================
# INSTALL STEP 1 — CLONE
# ==============================================================================
# Sparse-clones (or updates) this repository: root-level files
# (docker-compose.yml, .env.example, etc.) plus scripts/, skipping src/,
# ansible/, tests/, and other build/CI-time directories — the app runs from
# the GHCR image, not a source checkout. Re-running against an existing
# checkout pulls latest instead of re-cloning. Saves INSTALL_DIR to the
# shared state file so every later phase (including standalone ones) can
# find this checkout without being told where it is again.
#
# Invoked by scripts/install.sh. Also fully self-contained:
#   bash scripts/phases/install/01-clone.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

# When piped via `curl ... | bash`, stdin is the script itself, not the
# terminal — reopen it from the tty so the prompt below works interactively.
# /dev/tty existing as a device node doesn't mean it's openable: a CI runner
# (or any process with no controlling terminal) has no tty to reopen from —
# `|| true` lets that fail quietly and fall through to whatever stdin
# already is (piped answers, or a prompt() env var skipping the read
# entirely), instead of aborting under `set -e`.
if [ ! -t 0 ] && [ -e /dev/tty ]; then
    exec < /dev/tty 2>/dev/null || true
fi

REPO_URL="${REPO_URL:-https://github.com/TeamCastaldi/homelab-registry-mcp.git}"
DEFAULT_INSTALL_DIR="${HOME}/homelab-registry-mcp"

# Same variable already used in every documented curl one-liner — see
# scripts/install.sh's own header comment for the full rationale. Reused
# here so this clone checks out the *same* ref install.sh itself was
# fetched from, not always main regardless of what VERSION was.
VERSION="${VERSION:-}"

header "[STEP 1] Clone repository"

prompt INSTALL_DIR "Install directory" "$DEFAULT_INSTALL_DIR"

if [ -d "${INSTALL_DIR}/.git" ]; then
    info "Existing checkout found at ${INSTALL_DIR} — pulling latest"
    git -C "$INSTALL_DIR" pull --ff-only
else
    # The control-plane node only ever needs docker-compose.yml, .env.example,
    # and scripts/ — the app itself runs from the GHCR image, not a source
    # checkout (see docker-compose.yml: no build:, no Dockerfile, no bind
    # mount of anything from this repo). A blobless partial clone with
    # cone-mode sparse-checkout gets root-level files (docker-compose.yml,
    # .env.example, etc.) for free and adds just scripts/ — skipping src/,
    # ansible/, tests/, and the rest, which are build/CI-time only.
    action "Cloning ${REPO_URL} into ${INSTALL_DIR} (sparse: root-level files + scripts/, skipping src/, ansible/, tests/, etc.)..."
    if [ -n "$VERSION" ] && [ "$VERSION" != "main" ]; then
        info "VERSION=${VERSION} — cloning that branch/tag instead of the repo default"
        git clone --filter=blob:none --sparse --branch "$VERSION" "$REPO_URL" "$INSTALL_DIR"
    else
        git clone --filter=blob:none --sparse "$REPO_URL" "$INSTALL_DIR"
    fi
    git -C "$INSTALL_DIR" sparse-checkout set scripts
    info "Cloned to ${INSTALL_DIR}"
fi

cd "$INSTALL_DIR"

[ -f scripts/bootstrap.sh ] || die "scripts/bootstrap.sh not found in ${INSTALL_DIR} — is this the right repo?"

state_set INSTALL_DIR "$INSTALL_DIR"

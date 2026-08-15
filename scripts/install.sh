#!/bin/bash

# ==============================================================================
# HOMELAB REGISTRY MCP — ONE-SHOT INSTALLER
# ==============================================================================
# Curl-bash entry point for a fresh control-plane node. Sparse-clones
# root-level files (docker-compose.yml, .env.example, etc.) plus scripts/,
# skipping src/, ansible/, tests/, and other build/CI-time directories — the
# app runs from the GHCR image, not a source checkout — then hands off to
# bootstrap.sh for OS-level provisioning (Docker/Ansible/uv/git-crypt/gh +
# SSH key), collects the secrets needed for a working .env, brings the MCP
# server up via Docker Compose, and only then applies the static IP
# (bootstrap.sh --network-only) — so the server is already running by the
# time the SSH session drops.
#
# This script is an orchestrator: it runs each numbered step below by
# invoking a self-contained script from scripts/phases/install/, in order.
# Every one of those is independently runnable too — see its own header
# comment — which is the easiest way to debug or re-run just one step (e.g.
# re-writing .env after tweaking your answers) instead of the whole install.
# Cross-step handoff (the Git/DSPy answers Step 3 collects, the paths
# Steps 4-5 produce, ...) goes through a small state file under
# ~/.homelab-registry-mcp/ (see scripts/lib/common.sh) — a real environment
# variable of the same name always wins over it, so every documented
# pre-seeding trick below still works unchanged.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/TeamCastaldi/homelab-registry-mcp/main/scripts/install.sh | bash
#   # or, from a local clone:
#   bash scripts/install.sh
#
# Every prompt can be pre-seeded via an environment variable of the same name
# (e.g. `INSTALL_DIR=/opt/homelab-registry-mcp GIT_PROVIDER=github bash install.sh`)
# for non-interactive/CI use — any variable already set is not re-prompted.
#
# Assumes a greenfield setup: no Traefik or Authentik yet, so this installer
# doesn't ask about them. Connect those once they exist via the
# discovery_connect_traefik / discovery_connect_authentik MCP tools.
#
# What it does (numbered to match this script's own [STEP N] output exactly
# — including Step 0 — so log output, this list, and the docs never drift
# out of sync with each other the way a human-friendly "1. ..." list would):
#   0. Install git if missing                                       [phases/install/00-prereqs.sh]
#   1. Sparse-clone (or update) root-level files + scripts/, skipping src/,
#      ansible/, tests/, and other build/CI-time directories        [phases/install/01-clone.sh]
#   2. Run `bootstrap.sh --skip-network` — Docker, Ansible, uv, git-crypt, gh,
#      SSH key. Deliberately skips the static-IP swap. Every install step
#      skips cleanly if already present, but still fixes up required state
#      (Docker group membership, NetworkManager service) even when it does.
#                                                                     [phases/install/02-os-provision.sh]
#   3. Prompt for Git/DSPy secrets and opt-in                        [phases/install/03-configure.sh]
#   4. Optionally create the private homelab config repo (git-crypt-encrypted
#      secrets, Ansible inventory, nodes/ compose files) — requires `gh auth
#      login` to already be done; reuses the Git repo from step 3 if it was
#      given there and points at GitHub                              [phases/install/04-homelab-repo.sh]
#   5. If that repo exists (from step 4, or one you already had): optionally
#      seed this node into the Ansible inventory hardware-discover-now reads,
#      so hardware onboarding has a real, verified entry for this Pi from
#      the start                                                     [phases/install/05-ansible-inventory.sh]
#   6. Write .env                                                    [phases/install/06-write-env.sh]
#   7. `docker compose up -d` and confirm the server is running      [phases/install/07-start-server.sh]
#   8. Run `bootstrap.sh --network-only` — applies the static IP last, unless
#      INSTALL_SKIP_NETWORK=true (CI/test mode — see below), which skips it
#                                                                     [phases/install/08-network.sh]
# ==============================================================================

set -euo pipefail

# --- CURL-PIPE BOOTSTRAP ---
# `curl -fsSL .../install.sh | bash` (or `bash -c "$(curl ...)"`, this
# project's documented one-liner) hands bash this script's *text*, not a
# file — there is no on-disk path for it, so ${BASH_SOURCE[0]} is unset and
# the SCRIPT_DIR-relative `source`/phase-script invocations below (which
# every phase script also needs to find scripts/lib/common.sh) have nothing
# to resolve against. `${BASH_SOURCE[0]+set}` tests *set-ness*, not value,
# so it's safe under `set -u` even when BASH_SOURCE[0] flat-out doesn't
# exist — exactly the case here.
#
# When that happens, this block does the absolute minimum to get a real,
# disk-resident copy of this repo — install git, clone/update it — then
# `exec`s the on-disk scripts/install.sh in its place. That second
# invocation runs as a normal file (`bash /path/to/install.sh`), so
# BASH_SOURCE[0] is set correctly and everything below proceeds exactly as
# it would for a plain `bash scripts/install.sh` from an existing checkout —
# including Steps 0-1 below running again, which is instant and harmless
# (git already installed; INSTALL_DIR already cloned, so it's just a
# fast-forward `pull`) since `exec`ing exports INSTALL_DIR into the new
# process, and prompt() (used by Step 1) skips re-asking anything already
# set in the environment.
if [ -z "${BASH_SOURCE[0]+set}" ]; then
    if [ ! -t 0 ] && [ -e /dev/tty ]; then
        exec < /dev/tty 2>/dev/null || true
    fi

    if ! command -v git &>/dev/null; then
        echo "  [⚙] Installing git..."
        if [ "${EUID:-$(id -u)}" -eq 0 ]; then
            apt-get update -qq && apt-get install -y -qq git
        else
            sudo apt-get update -qq && sudo apt-get install -y -qq git
        fi
    fi

    REPO_URL="${REPO_URL:-https://github.com/TeamCastaldi/homelab-registry-mcp.git}"
    VERSION="${VERSION:-}"
    if [ -z "${INSTALL_DIR+set}" ]; then
        read -rp "Install directory [${HOME}/homelab-registry-mcp]: " INSTALL_DIR
        INSTALL_DIR="${INSTALL_DIR:-${HOME}/homelab-registry-mcp}"
    fi
    export INSTALL_DIR

    if [ -d "${INSTALL_DIR}/.git" ]; then
        git -C "$INSTALL_DIR" pull --ff-only
    else
        if [ -n "$VERSION" ] && [ "$VERSION" != "main" ]; then
            git clone --filter=blob:none --sparse --branch "$VERSION" "$REPO_URL" "$INSTALL_DIR"
        else
            git clone --filter=blob:none --sparse "$REPO_URL" "$INSTALL_DIR"
        fi
        git -C "$INSTALL_DIR" sparse-checkout set scripts
    fi

    [ -f "${INSTALL_DIR}/scripts/install.sh" ] || {
        echo "" >&2
        echo "ERROR: scripts/install.sh not found in ${INSTALL_DIR} — is this the right repo?" >&2
        exit 1
    }
    exec bash "${INSTALL_DIR}/scripts/install.sh"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PHASES_DIR="${SCRIPT_DIR}/phases/install"

# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

# Reopens stdin from the tty for this (now file-backed) invocation too — the
# bootstrap block above only covers the curl-pipe process before it exec'd
# into this one; a plain `bash scripts/install.sh < file` or similar still
# wants this same fallback for every prompt below.
if [ ! -t 0 ] && [ -e /dev/tty ]; then
    exec < /dev/tty 2>/dev/null || true
fi

# Fresh state for a fresh run — a prior run's answers, or an option declined
# this time, must never silently leak into this one. Cleared again on exit
# (success or failure) via the trap below, since the state file can hold
# secrets (GIT_TOKEN, ANTHROPIC_API_KEY, ...) that only need to live for the
# duration of this run.
state_clear
trap state_clear EXIT

echo ""
echo "================================================"
echo "  HOMELAB REGISTRY MCP — INSTALLER"
echo "================================================"

bash "${PHASES_DIR}/00-prereqs.sh"
bash "${PHASES_DIR}/01-clone.sh"
bash "${PHASES_DIR}/02-os-provision.sh"
bash "${PHASES_DIR}/03-configure.sh"
bash "${PHASES_DIR}/04-homelab-repo.sh"
bash "${PHASES_DIR}/05-ansible-inventory.sh"
bash "${PHASES_DIR}/06-write-env.sh"
bash "${PHASES_DIR}/07-start-server.sh"
bash "${PHASES_DIR}/08-network.sh"

#!/bin/bash

# ==============================================================================
# INSTALL STEP 6 — WRITE .env
# ==============================================================================
# Writes .env from everything Steps 3-5 collected — read back from the
# shared state file (or a pre-seeded env var, which always wins). Leaves an
# existing .env completely untouched: re-running install.sh (or just this
# phase) never clobbers a hand-edited config.
#
# Invoked by scripts/install.sh. Also fully self-contained — run it after
# 03-configure.sh (and, if you want them, 04-homelab-repo.sh /
# 05-ansible-inventory.sh) to (re)write .env from what they collected:
#   bash scripts/phases/install/06-write-env.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"
require_install_dir

header "[STEP 6] Writing .env"

if [ -f .env ]; then
    warn ".env already exists — leaving it untouched. Edit it by hand if these values changed."
else
    GIT_PROVIDER="$(resolve_var GIT_PROVIDER "")"
    GIT_REPO="$(resolve_var GIT_REPO "")"
    GIT_TOKEN="$(resolve_var GIT_TOKEN "")"
    GIT_BASE_URL="$(resolve_var GIT_BASE_URL "")"
    DSPY_ENABLED="$(resolve_var DSPY_ENABLED "false")"
    ANTHROPIC_API_KEY="$(resolve_var ANTHROPIC_API_KEY "")"
    SECRETS_REPO_PATH="$(resolve_var SECRETS_REPO_PATH "")"
    SECRETS_KEY_PATH="$(resolve_var SECRETS_KEY_PATH "")"
    ANSIBLE_CFG_PATH="$(resolve_var ANSIBLE_CFG_PATH "")"
    SSH_KEY_PATH="$(resolve_var SSH_KEY_PATH "")"

    cp .env.example .env
    # allow_empty=true on the optional integrations so leaving a prompt blank
    # actually disables it, instead of silently keeping the .env.example placeholder.
    # TRAEFIK_API_URL / AUTHENTIK_API_URL / AUTHENTIK_TOKEN are deliberately not
    # collected here (greenfield assumption) -- see discovery_connect_traefik /
    # discovery_connect_authentik once those services exist. Blanked explicitly
    # since .env.example ships non-empty placeholder URLs for both, which would
    # otherwise enable discovery against a nonexistent host by default.
    set_env TRAEFIK_API_URL "" true
    set_env AUTHENTIK_API_URL "" true
    set_env AUTHENTIK_TOKEN "" true
    # GIT_PROVIDER is NOT blanked like the three above -- config.py's
    # git_provider is a required Literal["gitea","github","gitlab"], not an
    # Optional[str]. pydantic-settings validates a present-but-empty env var
    # against the literal (it doesn't fall back to the field default for
    # that), so `GIT_PROVIDER=` in .env crashes the server at startup. The
    # write path is actually gated on GIT_BASE_URL/GIT_TOKEN/GIT_REPO being
    # set (see providers/git/build_git_provider) -- GIT_PROVIDER left at
    # .env.example's shipped default is harmless when those three are blank,
    # so skipping the prompt only needs to leave this one untouched.
    set_env GIT_PROVIDER "${GIT_PROVIDER:-}"
    set_env GIT_REPO "${GIT_REPO:-}" true
    set_env GIT_TOKEN "${GIT_TOKEN:-}" true
    set_env GIT_BASE_URL "${GIT_BASE_URL:-}" true
    set_env DSPY_ENABLED "${DSPY_ENABLED}"
    set_env ANTHROPIC_API_KEY "${ANTHROPIC_API_KEY:-}" true
    # Not allow_empty=true: .env.example already ships a real, correct
    # default (/opt/homelab) for this one, unlike the optional-integration
    # fields above -- if Step 4 was skipped, that default must survive
    # untouched, not get force-blanked just because SECRETS_REPO_PATH is
    # unset in this run.
    set_env SECRETS_REPO_PATH "${SECRETS_REPO_PATH:-}"
    set_env SECRETS_KEY_PATH "${SECRETS_KEY_PATH:-}" true
    # Startup health check prerequisites (Phase 2) — unset unless Step 5 ran
    # and actually set up the inventory. When it did, these are already
    # present for Step 7's first `docker compose up -d` below, so the server
    # starts read-write from the very first boot instead of needing a later
    # restart to pick them up.
    set_env ANSIBLE_CFG_PATH "${ANSIBLE_CFG_PATH:-}" true
    set_env SSH_KEY_PATH "${SSH_KEY_PATH:-}" true
    info ".env written"
fi

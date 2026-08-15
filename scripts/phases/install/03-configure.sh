#!/bin/bash

# ==============================================================================
# INSTALL STEP 3 — CONFIGURATION
# ==============================================================================
# Prompts for the Git write-path config and an optional DSPy opt-in. Every
# answer is saved to the shared state file for
# scripts/phases/install/06-write-env.sh (and, for GIT_*, 04-homelab-repo.sh)
# to pick up later in this same run.
#
# Invoked by scripts/install.sh. Also fully self-contained — pre-seed any
# prompt with an env var of the same name, same as install.sh itself:
#   bash scripts/phases/install/03-configure.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"
require_install_dir

header "[STEP 3] Configuration"
echo "These populate .env — press Enter to leave any optional value blank/default."
echo ""
echo "This installer assumes a greenfield setup: no Traefik or Authentik yet,"
echo "so it doesn't ask about them here. Once you stand those up, connect them"
echo "via the discovery_connect_traefik / discovery_connect_authentik MCP tools"
echo "(ask your AI client to run them) — they validate the connection and hand"
echo "back the exact .env lines to add, plus a restart to enable discovery."
echo ""

# github is the common case, so it's the default on a bare Enter; an
# operator who wants no write path at all types "skip" rather than leaving
# this blank, since blank now means "accept the default" instead of "skip"
# -- see the pre-seeding comment on prompt() in lib/common.sh for the
# non-interactive way to skip (GIT_PROVIDER=).
prompt GIT_PROVIDER "Git provider for the write path (github/gitea, or 'skip')" "github"
if [ "$GIT_PROVIDER" == "skip" ]; then
    GIT_PROVIDER=""
fi
if [ -n "${GIT_PROVIDER:-}" ]; then
    prompt GIT_REPO "Homelab config repo (owner/name)"
    prompt_secret GIT_TOKEN "Git token (classic: repo scope; fine-grained: Contents + Pull requests, read+write)"
    # The GitHub provider talks to the API root directly (no path prefix
    # added), so the default here must be api.github.com, not github.com —
    # GHES users override with their own /api/v3 root. Gitea/Forgejo has no
    # sensible universal default (self-hosted), so it's prompted with none.
    if [ "$GIT_PROVIDER" == "github" ]; then
        prompt GIT_BASE_URL "Git base URL (blank = public GitHub; GHES: e.g. https://ghe.example.com/api/v3)" "https://api.github.com"
    else
        # No default exists for a self-hosted instance, and leaving this
        # blank would silently disable the write path the operator just
        # asked for (the provider factory requires git_base_url) — keep
        # asking until they give a real host. Deliberately not prompt():
        # prompt() only asks once the var is entirely unset and, on a blank
        # answer, sets it to "" — which counts as "set" on the next check,
        # so a naive `while ...; do prompt ...; done` around it never
        # actually re-asks once a set-but-empty value (e.g. a pre-seeded
        # `GIT_BASE_URL=`) is in play — see CONTROL_PLANE_HOST below for the
        # same fix. Loop on a raw read instead until a non-empty value lands.
        while [ -z "${GIT_BASE_URL:-}" ]; do
            read -rp "Git base URL (your Gitea/Forgejo instance, e.g. https://gitea.example.com — required): " GIT_BASE_URL
        done
    fi
fi

# Asks only when DSPY_ENABLED isn't already set at all — pre-seeding
# DSPY_ENABLED=false (not just =true) skips the ask, same as every prompt()
# above; a bare `${DSPY_ENABLED:-false}` default here would make that
# impossible, since it makes the variable "set" before the check runs.
if [ -z "${DSPY_ENABLED+set}" ]; then
    read -rp "Enable Advanced AI Reasoning (DSPy)? [y/N]: " enable_dspy
    if [[ "$enable_dspy" =~ ^[Yy]$ ]]; then
        DSPY_ENABLED=true
        prompt_secret ANTHROPIC_API_KEY "Anthropic API key (used by DSPy)"
    else
        DSPY_ENABLED=false
    fi
fi

# Persist everything this step collected for 04-homelab-repo.sh (GIT_*) and
# 06-write-env.sh (everything) to pick up — each runs as its own process and
# can't see this one's shell variables directly.
state_set GIT_PROVIDER "${GIT_PROVIDER:-}"
state_set GIT_REPO "${GIT_REPO:-}"
state_set GIT_TOKEN "${GIT_TOKEN:-}"
state_set GIT_BASE_URL "${GIT_BASE_URL:-}"
state_set DSPY_ENABLED "${DSPY_ENABLED:-}"
state_set ANTHROPIC_API_KEY "${ANTHROPIC_API_KEY:-}"

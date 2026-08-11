#!/bin/bash

# ==============================================================================
# INSTALL STEP 3 — CONFIGURATION
# ==============================================================================
# Prompts for the Git write-path config, an optional DSPy opt-in, and the
# ADR-006 Komodo/Traefik yes/no gates. Every answer is saved to the shared
# state file for scripts/phases/install/06-write-env.sh (and, for GIT_*,
# 04-homelab-repo.sh) to pick up later in this same run.
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
        # asking until they give a real host.
        while [ -z "${GIT_BASE_URL:-}" ]; do
            prompt GIT_BASE_URL "Git base URL (your Gitea/Forgejo instance, e.g. https://gitea.example.com — required)"
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

echo ""
echo "ADR-006 Pi non-MCP services: Komodo (container management, logs, update"
echo "detection) and Traefik (this node's central ingress). Each is opt-in"
echo "independently — answer N to skip either and bring up only"
echo "homelab-registry-mcp, same as before."
echo ""

COMPOSE_PROFILES="${COMPOSE_PROFILES:-}"
read -rp "Enable Komodo (container management/logs/update detection) on this node? [y/N]: " enable_komodo
read -rp "Enable Traefik as this node's central ingress? [y/N]: " enable_traefik

if [[ "$enable_komodo" =~ ^[Yy]$ ]] || [[ "$enable_traefik" =~ ^[Yy]$ ]]; then
    # Both want this node's LAN IP/hostname (Komodo's OAuth/webhook URL
    # suggestion; useful context for Traefik's own config later). Step 2
    # (bootstrap.sh --skip-network) already asked for this node's static IP
    # and persisted it to NETWORK_STATE_FILE — reuse that answer instead of
    # making the operator retype it. Only falls back to asking if the state
    # file is missing (e.g. bootstrap.sh's network prompts were pre-seeded
    # via env vars and skipped, so nothing was ever written) or the operator
    # already pre-seeded CONTROL_PLANE_HOST themselves.
    if [ -z "${CONTROL_PLANE_HOST+set}" ]; then
        NETWORK_STATE_FILE="${INSTALL_DIR}/ansible/archive/outputs/.bootstrap-network-state"
        if [ -f "$NETWORK_STATE_FILE" ]; then
            _saved_ip="$(awk -F= '$1=="SAVED_TARGET_IP" { print $2 }' "$NETWORK_STATE_FILE")"
            if [ -n "$_saved_ip" ]; then
                CONTROL_PLANE_HOST="$_saved_ip"
                info "Using ${CONTROL_PLANE_HOST} (the static IP from Step 2)"
            fi
        fi
    fi
    # Unlike every other prompt in this script, an empty value here isn't a
    # valid "skip" — Komodo/Traefik being enabled means KOMODO_HOST and the
    # traefik-kop instructions need a real host, or they render broken
    # (http://:9120). So this deliberately doesn't use the prompt() helper:
    # prompt() only asks once the var is entirely unset and, on a blank
    # answer, sets it to "" — which counts as "set" on the next check, so a
    # naive `while ...; do prompt ...; done` around it would never actually
    # re-ask. Loop on a raw `read` instead until a non-empty value lands.
    while [ -z "${CONTROL_PLANE_HOST:-}" ]; do
        read -rp "This node's LAN IP or hostname (required for Komodo/Traefik): " CONTROL_PLANE_HOST
    done
fi

if [[ "$enable_komodo" =~ ^[Yy]$ ]]; then
    COMPOSE_PROFILES="komodo${COMPOSE_PROFILES:+,${COMPOSE_PROFILES}}"
    prompt KOMODO_INIT_ADMIN_USERNAME "Komodo admin username" "admin"
    prompt_secret KOMODO_INIT_ADMIN_PASSWORD "Komodo admin password (blank to auto-generate)"
    if [ -z "${KOMODO_INIT_ADMIN_PASSWORD:-}" ]; then
        KOMODO_INIT_ADMIN_PASSWORD="$(openssl rand -hex 16)"
        info "Generated a random Komodo admin password"
    fi
    # Database credentials and the Core<->Periphery/JWT secrets are internal
    # to this compose project (nothing external ever needs to know them),
    # so they're always auto-generated rather than prompted for.
    KOMODO_DATABASE_PASSWORD="$(openssl rand -hex 32)"
    KOMODO_WEBHOOK_SECRET="$(openssl rand -hex 32)"
    KOMODO_JWT_SECRET="$(openssl rand -hex 32)"
    KOMODO_HOST="http://${CONTROL_PLANE_HOST}:9120"
fi

if [[ "$enable_traefik" =~ ^[Yy]$ ]]; then
    COMPOSE_PROFILES="traefik${COMPOSE_PROFILES:+,${COMPOSE_PROFILES}}"
    # Backs the Redis that traefik-kop instances on other nodes publish
    # routing rules to (docker-compose.yml's `traefik-redis` service) —
    # internal to this compose project, always auto-generated.
    TRAEFIK_REDIS_PASSWORD="$(openssl rand -hex 32)"
    info "Generated a random Traefik/Redis password — point each workload"
    info "node's traefik-kop at ${CONTROL_PLANE_HOST}:6379 with it once this is up."
fi

# Persist everything this step collected/derived for 04-homelab-repo.sh (GIT_*)
# and 06-write-env.sh (everything) to pick up — each runs as its own process
# and can't see this one's shell variables directly.
state_set GIT_PROVIDER "${GIT_PROVIDER:-}"
state_set GIT_REPO "${GIT_REPO:-}"
state_set GIT_TOKEN "${GIT_TOKEN:-}"
state_set GIT_BASE_URL "${GIT_BASE_URL:-}"
state_set DSPY_ENABLED "${DSPY_ENABLED:-}"
state_set ANTHROPIC_API_KEY "${ANTHROPIC_API_KEY:-}"
state_set COMPOSE_PROFILES "${COMPOSE_PROFILES:-}"
state_set CONTROL_PLANE_HOST "${CONTROL_PLANE_HOST:-}"
state_set KOMODO_INIT_ADMIN_USERNAME "${KOMODO_INIT_ADMIN_USERNAME:-}"
state_set KOMODO_INIT_ADMIN_PASSWORD "${KOMODO_INIT_ADMIN_PASSWORD:-}"
state_set KOMODO_DATABASE_PASSWORD "${KOMODO_DATABASE_PASSWORD:-}"
state_set KOMODO_WEBHOOK_SECRET "${KOMODO_WEBHOOK_SECRET:-}"
state_set KOMODO_JWT_SECRET "${KOMODO_JWT_SECRET:-}"
state_set KOMODO_HOST "${KOMODO_HOST:-}"
state_set TRAEFIK_REDIS_PASSWORD "${TRAEFIK_REDIS_PASSWORD:-}"

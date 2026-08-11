#!/bin/bash

# ==============================================================================
# INSTALL STEP 7 — START THE MCP SERVER
# ==============================================================================
# `docker compose pull && docker compose up -d`, then waits for
# homelab-registry-mcp to report running. Anything enabled in Step 3
# (Komodo, Traefik) comes up in this same step, via the .env Step 6 wrote.
#
# Invoked by scripts/install.sh. Also fully self-contained:
#   INSTALL_DIR=~/homelab-registry-mcp bash scripts/phases/install/07-start-server.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"
require_install_dir

header "[STEP 7] Starting the MCP server"

# bootstrap.sh may have just added this user to the docker group in this
# same run — group membership changes don't apply to an already-open shell
# (this script's own process) until logout/login, so a plain `docker`
# command here would fail with "permission denied" on a truly fresh node.
# `sg docker -c "..."` applies the group for just that command, sidestepping
# the need to restart the shell mid-script.
action "docker compose pull && docker compose up -d"
sg docker -c "docker compose pull && docker compose up -d"

action "Waiting for homelab-registry-mcp to report running..."
READY=false
for _ in $(seq 1 30); do
    if sg docker -c "docker compose ps --status running --services" 2>/dev/null | grep -qx "homelab-registry-mcp"; then
        READY=true
        break
    fi
    sleep 2
done

if [ "$READY" == "true" ]; then
    info "homelab-registry-mcp is running"
else
    warn "Could not confirm the container is running — check 'docker compose logs' before continuing"
    read -rp "Continue with the network swap anyway? [y/N]: " force_continue
    [[ "$force_continue" =~ ^[Yy]$ ]] || die "Aborted — server not confirmed up. Re-run 'bash scripts/bootstrap.sh --network-only' manually once it is."
fi

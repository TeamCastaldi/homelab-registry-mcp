#!/bin/bash

# ==============================================================================
# INSTALL STEP 5 — ANSIBLE INVENTORY (HARDWARE ONBOARDING)
# ==============================================================================
# Optionally bootstraps ansible.cfg + ansible/inventory.yml inside the
# homelab config repo Step 4 just created (or one you already had) —
# hardware-discover-now and the reusable CD workflow both expect these to
# already exist. Same logic as scripts/setup-ansible-inventory.sh, folded
# in here. Seeds the inventory with this node itself (auto-detected), then
# prompts for more hosts.
#
# Invoked by scripts/install.sh. Also fully self-contained — reuses Step 4's
# SECRETS_REPO_PATH from the shared state file if present (or a pre-seeded
# env var), otherwise falls back to /opt/homelab same as install.sh:
#   bash scripts/phases/install/05-ansible-inventory.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"
require_install_dir

SECRETS_REPO_PATH="$(resolve_var SECRETS_REPO_PATH "")"

header "[STEP 5] Ansible inventory (hardware onboarding)"

# hardware-discover-now and the reusable CD workflow both read
# ansible.cfg + ansible/inventory.yml from the homelab config repo
# (SECRETS_REPO_PATH) — neither Ansible nor this project ships one for you.
# Step 4 creates that repo when accepted; if it was skipped (declined, or
# its own preconditions weren't met — no gh auth, etc.), or SECRETS_REPO_PATH
# was pre-seeded to somewhere Step 4 never touched, there's nothing here to
# work with yet. Skip cleanly rather than blocking everything else on a
# prerequisite this step didn't create itself.
ANSIBLE_INVENTORY_REPO="${SECRETS_REPO_PATH:-/opt/homelab}"
if [ ! -d "${ANSIBLE_INVENTORY_REPO}/.git" ]; then
    warn "No homelab config repo found at ${ANSIBLE_INVENTORY_REPO} — skipping."
    warn "Run scripts/setup-homelab-repo.sh, then scripts/setup-ansible-inventory.sh,"
    warn "(or re-run install.sh) to enable hardware onboarding later."
else
    echo "Found a homelab config repo at ${ANSIBLE_INVENTORY_REPO}. Setting this up"
    echo "seeds this node into the Ansible inventory hardware-discover-now reads,"
    echo "so the hardware registry gets a real, verified entry for this Pi."
    echo ""
    read -rp "Set up the Ansible inventory now? [y/N]: " enable_ansible_inventory
    if [[ "$enable_ansible_inventory" =~ ^[Yy]$ ]]; then
        prompt ANSIBLE_SSH_USER "SSH user Ansible should connect as on every host" "$(whoami)"
        prompt SSH_KEY_PATH "Path to the SSH private key Ansible should use" "${HOME}/.ssh/id_ed25519"
        # Persisted as soon as it's known — 06-write-env.sh runs as its own
        # process and needs this even if something below fails partway
        # through.
        state_set SSH_KEY_PATH "$SSH_KEY_PATH"

        CAN_AUTHORIZE=true
        if [ ! -f "${SSH_KEY_PATH}.pub" ]; then
            warn "${SSH_KEY_PATH}.pub not found — can't auto-authorize this key on new hosts."
            warn "You'll need to run ssh-copy-id yourself for each host added below."
            CAN_AUTHORIZE=false
        fi

        # Authorizes SSH_KEY_PATH on one remote host (ssh-copy-id only copies
        # the *public* key — never touches the private half). Idempotent:
        # ssh-copy-id already skips a key that's authorized there. Never
        # aborts the script — an unreachable host here just means retrying it
        # manually later.
        authorize_host() {
            local ip="$1"
            if [ "$CAN_AUTHORIZE" != "true" ]; then
                return
            fi
            if ssh-copy-id -i "${SSH_KEY_PATH}.pub" -o StrictHostKeyChecking=accept-new \
                "${ANSIBLE_SSH_USER}@${ip}" >/dev/null 2>&1; then
                info "Authorized this key on ${ip}"
            else
                warn "Couldn't authorize the key on ${ip} — run manually: ssh-copy-id -i ${SSH_KEY_PATH}.pub ${ANSIBLE_SSH_USER}@${ip}"
            fi
        }

        cd "$ANSIBLE_INVENTORY_REPO"

        if [ -f ansible.cfg ]; then
            info "ansible.cfg already exists — leaving it as-is"
        else
            action "Writing ansible.cfg..."
            # roles_path is intentionally absent: .github/workflows/deploy.yml
            # sets ANSIBLE_ROLES_PATH itself at invocation time, overriding
            # whatever's here. host_key_checking=False trades a little safety
            # for a CD pipeline that can reach a brand-new host
            # non-interactively — the ad-hoc hardware-discover-now probe
            # already pins StrictHostKeyChecking=accept-new itself regardless
            # of this setting. forks=1 avoids a real ansible-core bug (POSIX
            # fork() of a multithreaded process is undefined behavior — see
            # ansible/ansible#59642): a homelab inventory is small enough
            # that serial execution costs nothing worth trading for it.
            cat > ansible.cfg <<'EOF'
[defaults]
inventory = ansible/inventory.yml
host_key_checking = False
interpreter_python = auto_silent
forks = 1
EOF
            info "Wrote ansible.cfg"
        fi

        mkdir -p ansible
        INVENTORY_FILE="ansible/inventory.yml"
        if [ ! -f "$INVENTORY_FILE" ]; then
            action "Creating ${INVENTORY_FILE}..."
            cat > "$INVENTORY_FILE" <<EOF
all:
  hosts:
  vars:
    ansible_user: ${ANSIBLE_SSH_USER}
EOF
        fi

        # Appends one host under the `  hosts:` key without disturbing the
        # rest of the file — a full YAML merge would need a real parser, so
        # this only works because the file's shape is one this script fully
        # controls (a top-level `all:` with `hosts:`/`vars:` siblings at
        # 2-space indent). Hand-editing the file is fine as long as that
        # shape stays intact.
        add_host() {
            local name="$1" ip="$2"
            if grep -q "^    ${name}:\$" "$INVENTORY_FILE"; then
                warn "${name} is already in the inventory — skipping"
                return
            fi
            awk -v name="$name" -v ip="$ip" '
                { print }
                /^  hosts:$/ && !done { print "    " name ":"; print "      ansible_host: " ip; done=1 }
            ' "$INVENTORY_FILE" > "${INVENTORY_FILE}.tmp"
            mv "${INVENTORY_FILE}.tmp" "$INVENTORY_FILE"
            info "Added ${name} (${ip})"
        }

        # Seed with this node itself, so hardware-discover-now picks up the
        # box running registry-mcp without a manual prompt for it — over SSH
        # to its own LAN IP like any other host, not ansible_connection:
        # local (that would run inside the registry-mcp *container*,
        # gathering its ephemeral hostname/OS instead of the physical
        # machine's).
        CP_HOSTNAME="$(hostname)"
        CP_IP="$(ip route get 8.8.8.8 2>/dev/null | awk '{print $7; exit}' || true)"
        if [ -z "$CP_IP" ]; then
            warn "Couldn't auto-detect this node's IP — enter it manually."
            # Not prompt(): CP_IP is already "set" (to "") by the failed
            # auto-detect above, and prompt() treats set-but-empty as
            # already answered — it would silently skip asking at all, the
            # same bug shape fixed for CONTROL_PLANE_HOST in Step 3. Loop on
            # a raw read instead until a non-empty value lands.
            while [ -z "$CP_IP" ]; do
                read -rp "IP address of ${CP_HOSTNAME} (this node): " CP_IP
            done
        fi
        add_host "$CP_HOSTNAME" "$CP_IP"
        authorize_host "$CP_IP"

        echo ""
        echo "Add any other hosts you want in the inventory now (workload nodes,"
        echo "NAS, etc.) — leave the name blank to finish; you can always add more"
        echo "later by re-running scripts/setup-ansible-inventory.sh."
        while true; do
            echo ""
            read -rp "Host name (blank to finish): " HOST_NAME
            [ -z "$HOST_NAME" ] && break
            read -rp "IP address for ${HOST_NAME}: " HOST_IP
            if [ -z "$HOST_IP" ]; then
                warn "No IP given — skipping ${HOST_NAME}"
                continue
            fi
            add_host "$HOST_NAME" "$HOST_IP"
            authorize_host "$HOST_IP"
        done

        git add ansible.cfg ansible/inventory.yml
        if git diff --cached --quiet; then
            info "Nothing new to commit"
        else
            git commit -m "chore: update Ansible inventory"
            # Not a bare `git push`: this step is now embedded in the middle
            # of install.sh's larger sequence, not standalone like
            # setup-ansible-inventory.sh — a transient network/auth failure
            # here must not, under set -e, take down the rest of the
            # installer (starting the server, applying the static IP) along
            # with it. The commit lands locally either way, which is all
            # ANSIBLE_CFG_PATH below actually needs — the push only matters
            # for the separate GitHub Actions deploy workflow reading this
            # same repo, and that's recoverable by hand later.
            if git push; then
                info "Committed and pushed"
            else
                warn "Committed locally but couldn't push — push manually later:"
                warn "cd ${ANSIBLE_INVENTORY_REPO} && git push"
            fi
        fi

        ANSIBLE_CFG_PATH="${ANSIBLE_INVENTORY_REPO}/ansible.cfg"
        state_set ANSIBLE_CFG_PATH "$ANSIBLE_CFG_PATH"
        cd "$INSTALL_DIR"

        if [ "$CAN_AUTHORIZE" != "true" ]; then
            warn "Some hosts may need ssh-copy-id run manually before hardware-discover-now can reach them."
        fi
    fi
fi

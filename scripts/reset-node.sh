#!/bin/bash

# ==============================================================================
# HOMELAB CONTROL PLANE — NODE RESET
# ==============================================================================
# Reverses what scripts/install.sh + scripts/bootstrap.sh did to a
# control-plane node, without re-flashing the SD card. Run this on the node
# itself when you want to tear down a homelab-registry-mcp install and start
# over (or retire the node).
#
# Usage:
#   bash scripts/reset-node.sh [--dry-run] [--purge-packages] [--wipe-secrets] [--yes]
#
#   --dry-run          Show what would be done without making changes
#   --purge-packages   Also apt-get purge the packages bootstrap.sh installed
#                       (docker, ansible, git-crypt, gh) and remove uv.
#                       network-manager is deliberately left installed — this
#                       script needs nmcli to hand the interface back to DHCP.
#   --wipe-secrets     Also delete the git-crypt secrets repo (SECRETS_REPO_PATH)
#                       and its exported key (SECRETS_KEY_PATH). Off by default:
#                       the key is the only local copy, and losing it without a
#                       backup makes every encrypted .env unrecoverable. Gated
#                       behind its own typed confirmation, separate from the
#                       main y/N prompt.
#   --yes              Skip the main confirmation prompt (still asks separately
#                       for --wipe-secrets, and still pauses before the network
#                       reset since that drops the SSH session)
#
# Every path can be overridden via env var, same pattern as install.sh:
#   INSTALL_DIR         default: $HOME/homelab-registry-mcp
#   RESET_HOSTNAME      default: raspberrypi
#   SECRETS_REPO_PATH   default: $HOME/homelab
#   SECRETS_KEY_PATH    default: $HOME/.config/homelab/git-crypt.key
#
# What it does NOT do:
#   - Delete the git-crypt secrets repo or key (unless --wipe-secrets)
#   - Remove installed packages (unless --purge-packages)
#   - Anything to upstream Traefik/Authentik — this node never wrote to them
# ==============================================================================

set -euo pipefail

TARGET_USER="${SUDO_USER:-${USER:-$(whoami)}}"
INSTALL_DIR="${INSTALL_DIR:-${HOME}/homelab-registry-mcp}"
RESET_HOSTNAME="${RESET_HOSTNAME:-raspberrypi}"
SECRETS_REPO_PATH="${SECRETS_REPO_PATH:-${HOME}/homelab}"
SECRETS_KEY_PATH="${SECRETS_KEY_PATH:-${HOME}/.config/homelab/git-crypt.key}"
SSH_KEY="${HOME}/.ssh/id_ed25519"

info()    { echo "  [✓] $*"; }
action()  { echo "  [⚙] $*"; }
warn()    { echo "  [!] $*"; }
header()  { echo ""; echo "$*"; echo "---"; }
die()     { echo ""; echo "ERROR: $*" >&2; exit 1; }

# --- ARGUMENT PARSING ---

DRY_RUN=false
PURGE_PACKAGES=false
WIPE_SECRETS=false
ASSUME_YES=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --purge-packages) PURGE_PACKAGES=true ;;
        --wipe-secrets) WIPE_SECRETS=true ;;
        --yes) ASSUME_YES=true ;;
        --help|-h)
            echo "Usage: bash scripts/reset-node.sh [--dry-run] [--purge-packages] [--wipe-secrets] [--yes]"
            echo ""
            echo "  --dry-run          Show what would be done without making changes"
            echo "  --purge-packages   Also remove docker/ansible/git-crypt/gh/uv"
            echo "  --wipe-secrets     Also delete the git-crypt secrets repo + key (asks separately)"
            echo "  --yes              Skip the main confirmation prompt"
            exit 0
            ;;
        *) die "Unknown option: $arg" ;;
    esac
done

require_root_or_sudo() {
    # sudo -v prompts for a password if needed (unlike `sudo -n true`, which
    # fails immediately for any user without an already-cached timestamp).
    if ! sudo -v; then
        die "This script requires sudo. Run as a user with sudo access."
    fi
}

# Refuses to remove empty/root/$HOME paths — INSTALL_DIR, SECRETS_REPO_PATH,
# and SECRETS_KEY_PATH are all env-overridable, so a mis-set value shouldn't
# be able to wipe out more than intended.
safe_rm_rf() {
    local target="$1" label="$2"
    if [ -z "$target" ] || [ "$target" == "/" ] || [ "$target" == "$HOME" ]; then
        warn "Refusing to remove unsafe path for ${label}: \"${target}\""
        return 1
    fi
    sudo rm -rf "$target"
}

# Same detection bootstrap.sh uses — interface names vary across hardware
# (eth0 on Raspberry Pi OS, enp0s3/ens18/etc. on VMs), so don't hardcode.
DETECTED_IFACE="$(ip route show default 2>/dev/null | \
    awk '/^default/ { for (i=1; i<=NF; i++) if ($i == "dev") { print $(i+1); exit } }' || true)"
STATIC_IFACE="${DETECTED_IFACE:-eth0}"
NM_CON_NAME="static-${STATIC_IFACE}"

echo ""
echo "================================================="
echo "  HOMELAB CONTROL PLANE — NODE RESET"
echo "================================================="
echo ""
echo "This will:"
echo "  - Stop containers and wipe Docker volumes in ${INSTALL_DIR}"
echo "  - Delete ${INSTALL_DIR} (repo checkout, .env, ansible/archive state)"
echo "  - Remove /mnt/appdata and /mnt/media (only if empty)"
echo "  - Remove ${SSH_KEY}(.pub)"
echo "  - Revert hostname to \"${RESET_HOSTNAME}\""
echo "  - Delete the \"${NM_CON_NAME}\" NetworkManager profile and fall back to DHCP"
echo "    (this drops your current SSH session — same as bootstrap.sh)"
if [ "$PURGE_PACKAGES" == "true" ]; then
    echo "  - Purge docker/ansible/git-crypt/gh packages and remove uv (--purge-packages)"
fi
if [ "$WIPE_SECRETS" == "true" ]; then
    echo "  - Delete the secrets repo (${SECRETS_REPO_PATH}) and key (${SECRETS_KEY_PATH}) (--wipe-secrets)"
    warn "That key is the only local copy — without a backup, encrypted secrets become unrecoverable."
fi
echo ""
echo "It will NOT touch Traefik, Authentik, or any other host."
if [ "$PURGE_PACKAGES" != "true" ]; then
    echo "Installed packages (docker, ansible, git-crypt, gh, uv) are left in place — re-run with --purge-packages to remove them."
fi
if [ "$WIPE_SECRETS" != "true" ]; then
    echo "The secrets repo and git-crypt key are left in place — re-run with --wipe-secrets to remove them."
fi
echo ""

if [ "$DRY_RUN" == "true" ]; then
    echo "[DRY-RUN] No changes will be made. Re-run without --dry-run to execute."
    exit 0
fi

require_root_or_sudo

if [ "$ASSUME_YES" != "true" ]; then
    read -rp "Proceed? [y/N]: " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

# =============================================================================
# PHASE 1: STOP CONTAINERS & WIPE VOLUMES
# =============================================================================

header "[1/7] Stopping containers and wiping Docker volumes"

if [ -d "$INSTALL_DIR" ] && [ -f "${INSTALL_DIR}/docker-compose.yml" ]; then
    action "docker compose down -v (in ${INSTALL_DIR})"
    (cd "$INSTALL_DIR" && sudo docker compose down -v) || warn "docker compose down failed — continuing"
else
    info "No compose project found at ${INSTALL_DIR} — skipping"
fi

# =============================================================================
# PHASE 2: DELETE THE APP REPO CHECKOUT
# =============================================================================

header "[2/7] Deleting repository checkout"

if [ -d "$INSTALL_DIR" ]; then
    action "Removing ${INSTALL_DIR}"
    if safe_rm_rf "$INSTALL_DIR" "INSTALL_DIR"; then
        info "${INSTALL_DIR} removed"
    fi
else
    info "${INSTALL_DIR} does not exist — skipping"
fi

# =============================================================================
# PHASE 3: MOUNT STUBS
# =============================================================================

header "[3/7] Removing NFS mount-point stubs"

for mnt in /mnt/appdata /mnt/media; do
    if mountpoint -q "$mnt" 2>/dev/null; then
        action "Unmounting ${mnt}"
        sudo umount "$mnt" || { warn "Could not unmount ${mnt} — leaving it in place"; continue; }
    fi
    if [ -d "$mnt" ]; then
        if [ -z "$(ls -A "$mnt" 2>/dev/null)" ]; then
            sudo rmdir "$mnt"
            info "${mnt} removed"
        else
            warn "${mnt} is not empty — leaving it in place (looks like it holds real data)"
        fi
    fi
done

# =============================================================================
# PHASE 4: SSH KEY
# =============================================================================

header "[4/7] Removing generated SSH key"

if [ -f "$SSH_KEY" ]; then
    # bootstrap.sh only generates this key if one doesn't already exist, and
    # tags the one it generates with a "user@homelab-control-plane-<date>"
    # comment. Only delete it if that signature is present — otherwise this
    # is the operator's own pre-existing key, not something bootstrap made.
    if [ -f "${SSH_KEY}.pub" ] && grep -q "@homelab-control-plane-[0-9]\{8\}" "${SSH_KEY}.pub" 2>/dev/null; then
        rm -f "$SSH_KEY" "${SSH_KEY}.pub"
        info "${SSH_KEY}(.pub) removed"
    else
        warn "${SSH_KEY} doesn't look like it was generated by bootstrap.sh (missing the expected key comment) — leaving it in place. Remove manually if you're sure it's safe to delete."
    fi
else
    info "${SSH_KEY} does not exist — skipping"
fi

# =============================================================================
# PHASE 5: HOSTNAME
# =============================================================================

header "[5/7] Reverting hostname"

CURRENT_HOSTNAME=$(hostname)
if [ "$CURRENT_HOSTNAME" == "$RESET_HOSTNAME" ]; then
    info "Hostname already \"${RESET_HOSTNAME}\""
else
    action "Setting hostname to \"${RESET_HOSTNAME}\""
    sudo hostnamectl set-hostname "$RESET_HOSTNAME"
    if grep -q "127.0.1.1" /etc/hosts; then
        sudo sed -i "s/127.0.1.1.*/127.0.1.1\t${RESET_HOSTNAME}/" /etc/hosts
    else
        echo -e "127.0.1.1\t${RESET_HOSTNAME}" | sudo tee -a /etc/hosts > /dev/null
    fi
    info "Hostname reverted to \"${RESET_HOSTNAME}\""
fi

# =============================================================================
# OPTIONAL: PURGE PACKAGES
# =============================================================================

if [ "$PURGE_PACKAGES" == "true" ]; then
    header "[Optional] Purging installed packages"

    action "apt-get purge -y docker-ce docker-ce-cli containerd.io docker-compose-plugin ansible ansible-lint git-crypt gh"
    sudo apt-get purge -y docker-ce docker-ce-cli containerd.io docker-compose-plugin \
        ansible ansible-lint git-crypt gh 2>/dev/null || warn "Some packages were not installed — continuing"
    sudo apt-get autoremove -y -qq || true

    action "Removing Docker apt repo files"
    sudo rm -f /etc/apt/sources.list.d/docker.list /etc/apt/keyrings/docker.gpg

    action "Removing uv"
    rm -f "${HOME}/.local/bin/uv" "${HOME}/.local/bin/uvx"

    info "network-manager was left installed — this script needs nmcli for the network reset below"
    info "Package purge complete"
fi

# =============================================================================
# OPTIONAL: WIPE SECRETS
# =============================================================================

if [ "$WIPE_SECRETS" == "true" ]; then
    header "[Optional] Wiping secrets repo and key"

    warn "This deletes the only local copy of the git-crypt key at ${SECRETS_KEY_PATH}."
    warn "If it isn't backed up elsewhere (password manager, another clone), every"
    warn "git-crypt-encrypted .env in ${SECRETS_REPO_PATH} becomes permanently unrecoverable."
    read -rp "Type DELETE SECRETS to confirm: " secrets_confirm
    if [ "$secrets_confirm" == "DELETE SECRETS" ]; then
        if [ -d "$SECRETS_REPO_PATH" ]; then
            if safe_rm_rf "$SECRETS_REPO_PATH" "SECRETS_REPO_PATH"; then
                info "${SECRETS_REPO_PATH} removed"
            fi
        fi
        if [ -f "$SECRETS_KEY_PATH" ]; then
            if [ -z "$SECRETS_KEY_PATH" ] || [ "$SECRETS_KEY_PATH" == "/" ]; then
                warn "Refusing to remove unsafe path for SECRETS_KEY_PATH: \"${SECRETS_KEY_PATH}\""
            else
                sudo rm -f "$SECRETS_KEY_PATH"
                info "${SECRETS_KEY_PATH} removed"
            fi
        fi
    else
        warn "Confirmation text did not match — skipping secrets wipe"
    fi
fi

# =============================================================================
# PHASE 6/7: NETWORK RESET  ← LAST — DROPS SSH SESSION
# =============================================================================

header "[6/7] Network reset"

if ! command -v nmcli &>/dev/null; then
    warn "nmcli not found — skipping network reset. Revert manually if needed."
    echo ""
    echo "======================================="
    echo "  RESET COMPLETE (network unchanged)"
    echo "======================================="
    echo ""
else
    echo "This is the final step. It will delete \"${NM_CON_NAME}\" and switch"
    echo "${STATIC_IFACE} back to DHCP — this will drop your current SSH session."
    echo ""
    read -rp "Press ENTER to reset the network and drop the session (Ctrl+C to skip)..."

    header "[7/7] Applying DHCP"

    sudo nmcli connection delete "$NM_CON_NAME" 2>/dev/null || true

    DHCP_CON_NAME="dhcp-${STATIC_IFACE}"
    sudo nmcli connection delete "$DHCP_CON_NAME" 2>/dev/null || true
    sudo nmcli connection add \
        type ethernet \
        con-name "$DHCP_CON_NAME" \
        ifname "$STATIC_IFACE" \
        ipv4.method auto \
        connection.autoconnect yes

    echo ""
    echo "======================================="
    echo "  RESET COMPLETE"
    echo "======================================="
    echo ""
    echo "  Check your router for ${TARGET_USER}'s new DHCP lease to reconnect."
    echo "  Applying network config in 3 seconds..."
    sleep 3

    sudo nmcli connection up "$DHCP_CON_NAME"
fi

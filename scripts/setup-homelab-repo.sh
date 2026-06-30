#!/usr/bin/env bash
# setup-homelab-repo.sh — one-time bootstrap for the private homelab repo
#
# Run this once on the control plane node (Watchtower) to:
#   1. Create a private GitHub repo on your personal account
#   2. Clone it to SECRETS_REPO_PATH (default $HOME/homelab)
#   3. Initialise git-crypt
#   4. Write .gitattributes to encrypt **/.env files
#   5. Create the nodes/ skeleton
#   6. Export the key to SECRETS_KEY_PATH (default $HOME/.config/homelab/git-crypt.key)
#   7. Make an initial commit and push
#
# The repo lives on the Pi, not the NAS. GitHub is the off-site backup.
# Ansible deploys over SSH and doesn't need the repo on shared storage.
#
# The OOBE (Phase G) will eventually replace this script with oobe_create_repo
# and oobe_encrypt_secrets MCP tool calls.
#
# Prerequisites:
#   gh        (GitHub CLI, authenticated: gh auth login)
#   git
#   git-crypt
#
# Platform notes:
#   macOS   — brew install gh git-crypt  (git ships with Xcode CLI tools)
#   Linux   — apt install git git-crypt  + gh from github.com/cli/cli
#   Windows — run inside WSL (recommended) or Git Bash; no native support
#
# Usage:
#   chmod +x scripts/setup-homelab-repo.sh
#   ./scripts/setup-homelab-repo.sh
#
#   Override any default:
#   REPO_NAME=my-homelab SECRETS_REPO_PATH=/mnt/appdata/homelab \
#     SECRETS_KEY_PATH=/mnt/appdata/secrets/git-crypt.key \
#     WORKLOAD_NODES="node1 node2" \
#     ./scripts/setup-homelab-repo.sh

set -euo pipefail

# ---------------------------------------------------------------------------
# Config (override via env vars)
# ---------------------------------------------------------------------------
REPO_NAME="${REPO_NAME:-homelab}"
SECRETS_REPO_PATH="${SECRETS_REPO_PATH:-${HOME}/homelab}"
# Key lives outside version control, chmod 400.
# Back it up to Vaultwarden — if the node dies, you need the key to decrypt.
# Pi override: SECRETS_KEY_PATH=/opt/homelab/.git-crypt.key
SECRETS_KEY_PATH="${SECRETS_KEY_PATH:-${HOME}/.config/homelab/git-crypt.key}"
# Workload node names to scaffold under nodes/ (space-separated)
WORKLOAD_NODES="${WORKLOAD_NODES:-}"

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------
for cmd in gh git git-crypt; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "ERROR: $cmd is not installed. Install it before running this script." >&2
        exit 1
    fi
done

if ! gh auth status &>/dev/null; then
    echo "ERROR: gh is not authenticated. Run: gh auth login" >&2
    exit 1
fi

# Resolve the authenticated user's login — no org required
GITHUB_USER="$(gh api user --jq '.login')"
echo "==> Authenticated as: ${GITHUB_USER}"

# ---------------------------------------------------------------------------
# Create repo
# ---------------------------------------------------------------------------
FULL_REPO="${GITHUB_USER}/${REPO_NAME}"
echo "==> Creating private GitHub repo ${FULL_REPO}..."
if gh repo view "${FULL_REPO}" &>/dev/null; then
    echo "    Repo already exists — skipping creation."
else
    gh repo create "${FULL_REPO}" \
        --private \
        --description "Homelab configuration (git-crypt encrypted)"
    echo "    Created."
fi

# ---------------------------------------------------------------------------
# Clone
# ---------------------------------------------------------------------------
if [[ -d "${SECRETS_REPO_PATH}/.git" ]]; then
    echo "==> Repo already cloned at ${SECRETS_REPO_PATH} — skipping clone."
else
    echo "==> Cloning ${FULL_REPO} → ${SECRETS_REPO_PATH}..."
    mkdir -p "$(dirname "${SECRETS_REPO_PATH}")"
    gh repo clone "${FULL_REPO}" "${SECRETS_REPO_PATH}"
fi

cd "${SECRETS_REPO_PATH}"

# ---------------------------------------------------------------------------
# git-crypt init
# ---------------------------------------------------------------------------
if [[ -d .git/git-crypt ]]; then
    echo "==> git-crypt already initialised — skipping init."
else
    echo "==> Initialising git-crypt..."
    git-crypt init
fi

# ---------------------------------------------------------------------------
# .gitattributes
# ---------------------------------------------------------------------------
if [[ ! -f .gitattributes ]] || ! grep -q "filter=git-crypt" .gitattributes; then
    echo "==> Writing .gitattributes..."
    cat >> .gitattributes <<'EOF'
# Files matching these patterns are encrypted by git-crypt.
# Run: git-crypt unlock <keyfile>  to decrypt after cloning.
**/.env filter=git-crypt diff=git-crypt
EOF
fi

# ---------------------------------------------------------------------------
# nodes/ skeleton
# ---------------------------------------------------------------------------
if [[ -n "${WORKLOAD_NODES}" ]]; then
    echo "==> Creating nodes/ skeleton for: ${WORKLOAD_NODES}..."
    for node in ${WORKLOAD_NODES}; do
        mkdir -p "nodes/${node}"
        touch "nodes/${node}/.gitkeep"
    done
else
    echo "==> Skipping nodes/ skeleton (set WORKLOAD_NODES to scaffold them)."
    mkdir -p nodes
    touch nodes/.gitkeep
fi

# ---------------------------------------------------------------------------
# Export key
# ---------------------------------------------------------------------------
echo "==> Exporting git-crypt key to ${SECRETS_KEY_PATH}..."
mkdir -p "$(dirname "${SECRETS_KEY_PATH}")"
git-crypt export-key "${SECRETS_KEY_PATH}"
chmod 400 "${SECRETS_KEY_PATH}"
echo "    Key written to ${SECRETS_KEY_PATH} (chmod 400)."

# ---------------------------------------------------------------------------
# Initial commit and push
# ---------------------------------------------------------------------------
echo "==> Committing initial configuration..."
git add .gitattributes nodes/
git diff --cached --quiet || git commit -m "chore: initialise homelab repo with git-crypt"
git push -u origin main 2>/dev/null || git push -u origin HEAD

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  Homelab repo ready: https://github.com/${FULL_REPO}"
echo "============================================================"
echo ""
echo "NEXT STEPS — do these before anything else:"
echo ""
echo "  1. Back up the git-crypt key to your password manager"
echo "     (Bitwarden, 1Password, Vaultwarden, etc.)."
echo ""
echo "     The key is a binary file — store it as a base64 string:"
echo "       base64 \"${SECRETS_KEY_PATH}\" | tr -d '\\n'"
echo "     Copy the output into a new Secure Note in your password manager."
echo ""
echo "     To restore: paste the string into SECRETS_GIT_CRYPT_KEY in .env."
echo "     The MCP decodes it automatically."
echo ""
echo "  2. Add these to the registry-mcp .env on this node:"
echo "     SECRETS_ENABLED=true"
echo "     SECRETS_REPO_PATH=${SECRETS_REPO_PATH}"
echo "     SECRETS_KEY_PATH=${SECRETS_KEY_PATH}"
echo "     # OR use the base64 env var instead of the key file:"
echo "     # SECRETS_GIT_CRYPT_KEY=\$(base64 \"${SECRETS_KEY_PATH}\" | tr -d '\\n')"
echo ""
echo "  3. Restart the registry-mcp container:"
echo "     docker compose restart registry-mcp"
echo ""
echo "  IMPORTANT: The key at ${SECRETS_KEY_PATH} is the only way to"
echo "  decrypt your secrets. If you lose it, your .env files are"
echo "  unrecoverable. Back it up NOW before doing anything else."
echo ""

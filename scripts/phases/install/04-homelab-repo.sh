#!/bin/bash

# ==============================================================================
# INSTALL STEP 4 — HOMELAB CONFIG REPO
# ==============================================================================
# Optionally creates the private GitHub repo that holds your homelab's
# Git-managed config: git-crypt-encrypted secrets, the Ansible inventory
# (next step), and the nodes/<node>/<service>/compose.yaml files SOP-001
# deploys from. Same logic as scripts/setup-homelab-repo.sh, folded in here
# with Pi-appropriate defaults (/opt/homelab) and GitHub-repo reuse from
# Step 3 when that was answered `github`.
#
# Invoked by scripts/install.sh. Also fully self-contained — reuses Step 3's
# GIT_PROVIDER/GIT_REPO answer from the shared state file if present (or a
# pre-seeded env var), otherwise proceeds without a repo to reuse:
#   bash scripts/phases/install/04-homelab-repo.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"
require_install_dir

GIT_PROVIDER="$(resolve_var GIT_PROVIDER "")"
GIT_REPO="$(resolve_var GIT_REPO "")"

header "[STEP 4] Homelab config repo"

echo "This creates the private GitHub repo that holds your homelab's Git-managed"
echo "config: git-crypt-encrypted secrets, the Ansible inventory (next step), and"
echo "the nodes/<node>/<service>/compose.yaml files SOP-001 deploys from. Skip"
echo "this if you already have one, or aren't ready yet — re-run"
echo "scripts/setup-homelab-repo.sh any time later."
echo ""

if ! command -v gh &>/dev/null || ! command -v git-crypt &>/dev/null; then
    warn "gh and/or git-crypt not found — scripts/bootstrap.sh should have installed"
    warn "both. Skipping; run scripts/setup-homelab-repo.sh once they're available."
elif ! gh auth status &>/dev/null; then
    # Previously this just skipped with instructions to run 'gh auth login'
    # manually and re-run install.sh -- but the whole point of install.sh is
    # to be the one script an operator runs, so make the one-time device-code
    # login part of this step instead of a separate manual prerequisite. No
    # env-var pre-seed for this gate, same as the Komodo/Traefik yes/no
    # prompts in Step 3 -- gh auth login's own device-code flow can't be
    # meaningfully pre-answered anyway. In CI, gh auth status already fails
    # (no GH_TOKEN/GITHUB_TOKEN in this job's env) and the printf answer
    # already queued for the "Create/use..." prompt below lands on this one
    # instead, declining it -- gh auth login is never invoked non-interactively.
    read -rp "gh isn't authenticated yet — run 'gh auth login' now? [Y/n]: " run_gh_auth_login
    if [[ ! "$run_gh_auth_login" =~ ^[Nn]$ ]]; then
        echo "Recommended: GitHub.com -> HTTPS -> Login with a web browser -> yes to"
        echo "'Authenticate Git with your GitHub credentials?' (lets plain git push/"
        echo "clone work too, not just gh-mediated calls)."
        gh auth login || true
    fi
    if ! gh auth status &>/dev/null; then
        warn "gh still isn't authenticated — skipping the homelab config repo prompt."
        warn "Run 'gh auth login' then scripts/setup-homelab-repo.sh (or re-run install.sh)."
    fi
fi

if command -v gh &>/dev/null && command -v git-crypt &>/dev/null && gh auth status &>/dev/null; then
    read -rp "Create/use a private homelab config repo now? [y/N]: " enable_homelab_repo
    if [[ "$enable_homelab_repo" =~ ^[Yy]$ ]]; then
        GITHUB_USER="$(gh api user --jq '.login')"
        if [ "${GIT_PROVIDER:-}" == "github" ] && [ -n "${GIT_REPO:-}" ]; then
            # Reuse the repo already named in Step 3 rather than asking for
            # the same owner/name twice — gh repo create only ever targets
            # GitHub anyway (this step, like the standalone script it's
            # ported from, has no Gitea/Forgejo equivalent), so a Gitea
            # write path has nothing to reuse here regardless.
            FULL_REPO="$GIT_REPO"
            info "Using ${FULL_REPO} (already given in Step 3) as the homelab config repo."
        else
            prompt REPO_NAME "Repo name for your private homelab config repo" "homelab"
            FULL_REPO="${GITHUB_USER}/${REPO_NAME}"
        fi
        prompt SECRETS_REPO_PATH "Where to clone it on this node" "/opt/homelab"
        prompt SECRETS_KEY_PATH "Where to export the git-crypt key" "${SECRETS_REPO_PATH}/.git-crypt.key"
        # Persisted as soon as they're known — 05-ansible-inventory.sh and
        # 06-write-env.sh run as their own processes and need these even if
        # something below fails partway through.
        state_set SECRETS_REPO_PATH "$SECRETS_REPO_PATH"
        state_set SECRETS_KEY_PATH "$SECRETS_KEY_PATH"

        if gh repo view "$FULL_REPO" &>/dev/null; then
            info "${FULL_REPO} already exists — skipping creation."
        else
            action "Creating private GitHub repo ${FULL_REPO}..."
            # Not a bare statement: a typo'd owner (gh repo create requires
            # it to be your own account or an org you belong to) returns a
            # 404 from gh with no other output -- under set -e that silently
            # kills the entire installer right here, with nothing to explain
            # why. Nothing has been created or written yet at this point
            # (the whole point of failing loudly instead), so re-running
            # install.sh after fixing the owner is always safe.
            gh repo create "$FULL_REPO" --private --description "Homelab configuration (git-crypt encrypted)" \
                || die "Couldn't create ${FULL_REPO} -- check the owner is your account or an org you belong to (a typo there returns 404), then re-run install.sh."
            info "Created."
        fi

        if [ -d "${SECRETS_REPO_PATH}/.git" ]; then
            info "Already cloned at ${SECRETS_REPO_PATH} — skipping clone."
        else
            action "Cloning ${FULL_REPO} -> ${SECRETS_REPO_PATH}..."
            # The /opt default (like /opt itself) is root-owned on a stock
            # Debian/Ubuntu system -- a non-root operator (the common case:
            # a sudo-capable user, not a root login) can't create a new
            # directory under it, and gh repo clone creating SECRETS_REPO_PATH
            # itself would otherwise fail here and abort the rest of the
            # installer under set -e. Try as this user first (handles a
            # writable parent, e.g. a custom path under $HOME); only reach
            # for sudo, and only chown the leaf directory (not its parent),
            # if that's not enough.
            #
            # Deliberately NOT gated on mkdir -p's own exit code: mkdir -p
            # returns success when the target already exists, regardless of
            # whether the current user can write into it -- e.g. a stale
            # root-owned /opt/homelab left over from an earlier attempt. That
            # left the sudo fallback below unreachable and gh repo clone
            # failed deeper in, on creating .git, with a confusing
            # "Permission denied" instead of a clean fallback. Testing
            # writability directly after the mkdir -p attempt catches both
            # "couldn't create it" and "it already exists but isn't mine".
            mkdir -p "$SECRETS_REPO_PATH" 2>/dev/null
            if [ ! -w "$SECRETS_REPO_PATH" ]; then
                warn "Can't write to ${SECRETS_REPO_PATH} as $(whoami) — retrying with sudo."
                sudo mkdir -p "$SECRETS_REPO_PATH"
                sudo chown "$(id -u):$(id -g)" "$SECRETS_REPO_PATH"
            fi
            # Same reasoning as gh repo create above -- fail loudly instead
            # of a silent set -e death with no explanation.
            gh repo clone "$FULL_REPO" "$SECRETS_REPO_PATH" \
                || die "Couldn't clone ${FULL_REPO} to ${SECRETS_REPO_PATH} -- check network access and that the repo actually exists (${FULL_REPO} on GitHub), then re-run install.sh."
        fi

        cd "$SECRETS_REPO_PATH"

        # A fresh machine commonly has no global git identity configured --
        # git commit below would otherwise fail with "Please tell me who you
        # are", the same silent-death shape as the two gh calls above. Scoped
        # to just this repo (no --global), so it never overrides anything
        # already configured elsewhere -- `git config user.email` already
        # resolves the effective value including any global config, so this
        # only fills the gap when nothing is set at any level.
        if [ -z "$(git config user.email 2>/dev/null)" ]; then
            git config user.email "${GITHUB_USER}@users.noreply.github.com"
        fi
        if [ -z "$(git config user.name 2>/dev/null)" ]; then
            git config user.name "$GITHUB_USER"
        fi

        if [ -d .git/git-crypt ]; then
            info "git-crypt already initialised — skipping."
        else
            action "Initialising git-crypt..."
            git-crypt init
        fi

        if [ ! -f .gitattributes ] || ! grep -q "filter=git-crypt" .gitattributes; then
            action "Writing .gitattributes..."
            cat >> .gitattributes <<'EOF'
# Files matching these patterns are encrypted by git-crypt.
# Run: git-crypt unlock <keyfile>  to decrypt after cloning.
**/.env filter=git-crypt diff=git-crypt
EOF
        fi

        # If the key export below lands inside this repo's own working tree
        # (the /opt default does: SECRETS_REPO_PATH/.git-crypt.key), make
        # sure a later careless `git add -A` can never commit the plaintext
        # key that decrypts everything git-crypt is protecting here --
        # .gitattributes only tells git-crypt to encrypt **/.env, it has no
        # opinion on the key file itself. A key stored outside the repo
        # (SECRETS_KEY_PATH pointed elsewhere) has nothing to ignore.
        GIT_ADD_PATHS=(.gitattributes nodes/)
        case "$SECRETS_KEY_PATH" in
            "${SECRETS_REPO_PATH}"/*)
                KEY_REL_PATH="${SECRETS_KEY_PATH#"${SECRETS_REPO_PATH}"/}"
                if [ ! -f .gitignore ] || ! grep -qF "$KEY_REL_PATH" .gitignore; then
                    action "Adding ${KEY_REL_PATH} to .gitignore..."
                    echo "$KEY_REL_PATH" >> .gitignore
                fi
                GIT_ADD_PATHS+=(.gitignore)
                ;;
        esac

        # nodes/ skeleton -- WORKLOAD_NODES is env-var-only (not prompted):
        # the Ansible inventory step right after this one already asks for
        # host names interactively, and asking twice for similar-but-not-
        # identical information (bare names here vs name+IP there) would
        # just be confusing. Set it beforehand for non-interactive use if
        # you want scaffolded nodes/<name>/ directories too.
        if [ -n "${WORKLOAD_NODES:-}" ]; then
            action "Creating nodes/ skeleton for: ${WORKLOAD_NODES}..."
            for node in $WORKLOAD_NODES; do
                mkdir -p "nodes/${node}"
                touch "nodes/${node}/.gitkeep"
            done
        else
            mkdir -p nodes
            touch nodes/.gitkeep
        fi

        action "Exporting git-crypt key to ${SECRETS_KEY_PATH}..."
        mkdir -p "$(dirname "${SECRETS_KEY_PATH}")"
        git-crypt export-key "$SECRETS_KEY_PATH"
        chmod 400 "$SECRETS_KEY_PATH"
        info "Key written to ${SECRETS_KEY_PATH} (chmod 400)."
        warn "Back this up to your password manager NOW — it's the only way to"
        warn "decrypt secrets if this node is lost. base64 \"${SECRETS_KEY_PATH}\" | tr -d '\\n'"

        git add "${GIT_ADD_PATHS[@]}"
        if git diff --cached --quiet; then
            info "Nothing new to commit"
        else
            git commit -m "chore: initialise homelab repo with git-crypt"
            # Same reasoning as the Ansible inventory step below: this step
            # is embedded in the middle of install.sh's larger sequence now,
            # not standalone like setup-homelab-repo.sh -- a transient
            # network/auth failure here must not, under set -e, take down
            # the rest of the installer with it. The commit lands locally
            # either way, which is what matters for SECRETS_REPO_PATH below.
            if git push -u origin main 2>/dev/null || git push -u origin HEAD; then
                info "Committed and pushed"
            else
                warn "Committed locally but couldn't push — push manually later:"
                warn "cd ${SECRETS_REPO_PATH} && git push"
            fi
        fi

        cd "$INSTALL_DIR"
        info "Homelab repo ready: https://github.com/${FULL_REPO}"
    fi
fi

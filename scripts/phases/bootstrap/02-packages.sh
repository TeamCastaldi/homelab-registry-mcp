#!/bin/bash

# ==============================================================================
# BOOTSTRAP PHASE 2 — PACKAGE INSTALLATION
# ==============================================================================
# Installs Docker, Ansible + ansible-lint, uv, git-crypt, the GitHub CLI,
# NetworkManager (needed by Phase 6), and a handful of utility packages;
# creates the /mnt/appdata and /mnt/media NFS mount-point stubs. Every
# install is guarded so re-running this is a no-op where things already
# exist — the docker-group and NetworkManager-service fixups run every time
# regardless, since those can regress even when the package itself is
# already installed.
#
# Invoked by scripts/bootstrap.sh as part of a normal (non `--network-only`)
# run. Also fully self-contained:
#   sudo -v && bash scripts/phases/bootstrap/02-packages.sh
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/common.sh
source "${SCRIPT_DIR}/../../lib/common.sh"

require_root_or_sudo
detect_docker_repo_os

# The user to add to the docker group — falls back through sudo's caller to
# $USER, never hardcode an account name.
TARGET_USER="${TARGET_USER:-${SUDO_USER:-$USER}}"

header "[PHASE 2] Package Installation"

action "Updating package lists..."
sudo apt-get update -qq

action "Installing prerequisites..."
sudo apt-get install -y -qq ca-certificates curl gnupg lsb-release

# --- DOCKER ---

if command -v docker &>/dev/null; then
    info "Docker already installed: $(docker --version)"
else
    action "Installing Docker (repo: ${DOCKER_REPO_OS}/${DOCKER_REPO_CODENAME})..."

    sudo rm -f /etc/apt/sources.list.d/docker.list
    sudo mkdir -p /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${DOCKER_REPO_OS}/gpg" | \
        sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${DOCKER_REPO_OS} ${DOCKER_REPO_CODENAME} stable" | \
        sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update -qq
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

    info "Docker installed: $(docker --version)"
fi

# Runs regardless of whether Docker was just installed or already present —
# a box with Docker pre-installed but the invoking user never added to the
# group would otherwise never get fixed on rerun.
if groups "$TARGET_USER" | grep -qw docker; then
    info "${TARGET_USER} already in the docker group"
else
    sudo usermod -aG docker "$TARGET_USER"
    warn "Docker group added — run 'newgrp docker' or log out/in before using 'docker ps'"
fi

# --- ANSIBLE ---

if command -v ansible &>/dev/null; then
    info "Ansible already installed: $(ansible --version | head -n1)"
else
    action "Installing Ansible + ansible-lint..."
    sudo apt-get install -y -qq ansible ansible-lint
    info "Ansible installed: $(ansible --version | head -n1)"
fi

if ! command -v ansible-lint &>/dev/null; then
    action "Installing ansible-lint..."
    sudo apt-get install -y -qq ansible-lint
fi
info "ansible-lint: $(ansible-lint --version 2>/dev/null | head -n1 || echo 'installed')"

# --- UV ---

if command -v uv &>/dev/null; then
    info "uv already installed: $(uv --version)"
else
    action "Installing uv (Python package manager for registry-mcp)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    info "uv installed: $(uv --version 2>/dev/null || echo 'installed — reload shell to activate')"
fi

# --- GIT-CRYPT ---

if command -v git-crypt &>/dev/null; then
    info "git-crypt already installed: $(git-crypt --version 2>/dev/null || echo 'installed')"
else
    action "Installing git-crypt (Phase C prerequisite)..."
    sudo apt-get install -y -qq git-crypt
    info "git-crypt installed: $(git-crypt --version 2>/dev/null || echo 'installed')"
fi

# --- GITHUB CLI ---

if command -v gh &>/dev/null; then
    info "gh already installed: $(gh --version | head -n1)"
else
    action "Installing GitHub CLI (gh)..."
    sudo apt-get install -y -qq gh
    info "gh installed: $(gh --version | head -n1)"
fi

# --- NETWORKMANAGER ---
# Phase 6 applies the static IP via nmcli. Raspberry Pi OS (Bookworm+) ships
# NetworkManager by default, so this was never missing there — but Ubuntu
# Server defaults to netplan + systemd-networkd with no NetworkManager at
# all, so nmcli isn't guaranteed on every OS this script supports. This only
# guarantees the binary + service exist; if netplan/systemd-networkd
# (Ubuntu) or ifupdown via /etc/network/interfaces (Debian, including
# Raspberry Pi OS) is still rendering the interface, it may remain
# unmanaged — Phase 6 checks for that explicitly.
if command -v nmcli &>/dev/null; then
    info "NetworkManager already installed: $(nmcli --version 2>/dev/null | head -n1 || echo installed)"
else
    action "Installing NetworkManager (required for the Phase 6 static IP step)..."
    sudo apt-get install -y -qq network-manager
fi

# Runs regardless of whether NetworkManager was just installed or already
# present — a box with the package pre-installed but the service disabled
# would otherwise only surface as a cryptic nmcli failure in Phase 6.
if systemctl is-active --quiet NetworkManager; then
    info "NetworkManager service is running"
elif sudo systemctl enable --now NetworkManager >/dev/null 2>&1; then
    info "NetworkManager service enabled and started"
else
    warn "NetworkManager installed but the service could not be enabled/started — check 'systemctl status NetworkManager' before Phase 6"
fi

# --- UTILITY PACKAGES ---

action "Installing utility packages..."
sudo apt-get install -y -qq git vim htop curl wget nfs-common net-tools dnsutils
info "Utility packages installed"

# --- MOUNT POINT STUBS ---

action "Creating NFS mount point stubs..."
sudo mkdir -p /mnt/appdata /mnt/media
info "/mnt/appdata and /mnt/media ready (OOBE step 5 will wire fstab)"

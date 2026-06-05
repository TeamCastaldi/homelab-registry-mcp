#!/bin/bash

# ==============================================================================
# UNIFIED HOMELAB BOOTSTRAP SCRIPT
# ==============================================================================
# Control plane day-0 preparation for all homelab hardware types.
# For the Pi, this script brings the node to OOBE-ready state.
# The OOBE (via MCP tools) takes over from here — see ADR-001 §5.
#
#
# Usage:
#   ./bootstrap.sh [OPTIONS]
#
# Options:
#   --help                Show this help message
#   --dry-run             Show what would be done without making changes
#   --hardware-type TYPE  Override auto-detection (proxmox|docker-vm|pi|physical-docker|ai-workstation)
#   --skip-network        Skip network configuration
#   --skip-validation     Skip post-bootstrap validation
#   --output-json         Generate JSON output instead of YAML
#   --target-ip IP        Target static IP address (default: auto-assigned)
#   --gateway IP          Set gateway IP (default: 10.0.0.1)
#   --dns IP              Set DNS server (default: 10.0.0.2)
#
# Examples:
#   ./bootstrap.sh                          # Auto-detect and configure
#   ./bootstrap.sh --dry-run                # Preview actions
#   ./bootstrap.sh --hardware-type pi       # Force Pi/control-plane mode
#   ./bootstrap.sh --target-ip 10.0.0.205   # Custom IP address
#
# Phase mapping (Pi/control-plane):
#   Phase A-1  System Detection
#   Phase A-2  Network Configuration
#   Phase A-3  Package Installation (Docker, Ansible, uv, git-crypt, gh)
#   Phase A-4  SSH Key Generation
#   Phase A-5  Validation
#   Phase A-6  Hardware Fingerprinting
#   → OOBE handoff (oobe_status → steps 1-15 per ADR-001 §5.1)
#
# ==============================================================================

set -euo pipefail

# --- SCRIPT METADATA ---

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
VERSION="4.1.0"
TIMESTAMP=$(date +%Y%m%dT%H%M%S)

# --- LOAD LIBRARIES ---

# shellcheck source=./lib/detection.sh
source "${SCRIPT_DIR}/lib/detection.sh"
# shellcheck source=./lib/network.sh
source "${SCRIPT_DIR}/lib/network.sh"
# shellcheck source=./lib/validation.sh
source "${SCRIPT_DIR}/lib/validation.sh"
# shellcheck source=./lib/fingerprint.sh
source "${SCRIPT_DIR}/lib/fingerprint.sh"
# shellcheck source=./lib/proxmox.sh
source "${SCRIPT_DIR}/lib/proxmox.sh"

# --- COMMAND LINE ARGUMENTS ---

SHOW_HELP=false
DRY_RUN=false
HARDWARE_TYPE="auto"
SKIP_NETWORK=false
SKIP_VALIDATION=false
OUTPUT_JSON=false
TARGET_IP=""
GATEWAY="10.0.0.1"
DNS_SERVER="10.0.0.2"

parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help|-h)
                SHOW_HELP=true
                shift
                ;;
            --dry-run)
                DRY_RUN=true
                shift
                ;;
            --hardware-type)
                HARDWARE_TYPE="$2"
                shift 2
                ;;
            --skip-network)
                SKIP_NETWORK=true
                shift
                ;;
            --skip-validation)
                SKIP_VALIDATION=true
                shift
                ;;
            --output-json)
                OUTPUT_JSON=true
                shift
                ;;
            --target-ip)
                TARGET_IP="$2"
                shift 2
                ;;
            --gateway)
                GATEWAY="$2"
                shift 2
                ;;
            --dns)
                DNS_SERVER="$2"
                shift 2
                ;;
            *)
                echo "ERROR: Unknown option: $1" >&2
                echo "Use --help for usage information" >&2
                exit 1
                ;;
        esac
    done
}

show_help() {
    cat <<'EOF'
=============================================================================
UNIFIED HOMELAB BOOTSTRAP SCRIPT v4.1.0
=============================================================================

Control plane day-0 preparation for all homelab hardware types.
For the Pi, brings the node to OOBE-ready state (ADR-001 §5).
The OOBE (via MCP tools) takes over after this script completes.

USAGE:
  ./bootstrap.sh [OPTIONS]

OPTIONS:
  --help                Show this help message
  --dry-run             Preview actions without making changes
  --hardware-type TYPE  Override auto-detection
                        Types: proxmox, docker-vm, pi, physical-docker, ai-workstation
  --skip-network        Skip network configuration (use if already configured)
  --skip-validation     Skip post-bootstrap validation checks
  --output-json         Generate JSON output instead of YAML
  --target-ip IP        Set static IP address (default: auto-assigned)
  --gateway IP          Set gateway IP (default: 10.0.0.1)
  --dns IP              Set DNS server (default: 10.0.0.2)

EXAMPLES:
  ./bootstrap.sh
    Auto-detect hardware and configure with defaults

  ./bootstrap.sh --dry-run
    Preview what would be done without making changes

  ./bootstrap.sh --hardware-type pi --target-ip 10.0.0.200
    Force Pi mode and set specific IP

  ./bootstrap.sh --skip-network
    Skip network configuration (already configured manually)

PHASE MAP (Pi / control-plane):
  Phase A-1  System Detection    → identify OS, hardware type, CPU
  Phase A-2  Network Config      → static IP via netplan (skippable)
  Phase A-3  Packages            → Docker, Ansible, uv, git-crypt, gh CLI
  Phase A-4  SSH Keys            → generate/verify ED25519 key pair
  Phase A-5  Validation          → comprehensive health checks + log
  Phase A-6  Fingerprinting      → hardware inventory YAML

  → OOBE handoff: run oobe_status to begin steps 1–15 (ADR-001 §5.1)

OUTPUT FILES:
  ansible/archive/outputs/bootstrap-validation-{hostname}-{timestamp}.log
  ansible/archive/outputs/hardware-facts-{hostname}-{timestamp}.yml
  ansible/archive/inventory/discovered-hosts.yml (auto-discovery)

NOTES:
  - Network configuration will disconnect SSH (reconnect to new IP)
  - Run from console or plan for reconnection
  - Safe to re-run (idempotent where possible)
  - Logs saved even if script interrupted
  - NFS mounts are NOT configured here — OOBE step 5 handles that
    (but /mnt/appdata and /mnt/media are created as mount point stubs)

=============================================================================
EOF
}

# --- MAIN WORKFLOW ---

main() {
    # Parse CLI arguments
    parse_arguments "$@"

    if [ "$SHOW_HELP" == "true" ]; then
        show_help
        exit 0
    fi

    # === PHASE A-1: SYSTEM DETECTION ===

    echo "======================================="
    echo "HOMELAB BOOTSTRAP v${VERSION}"
    echo "Timestamp: $(date -u +"%Y-%m-%d %H:%M:%S UTC")"
    echo "======================================="
    echo ""

    echo "[PHASE A-1] System Detection"
    echo "---"

    # Detect hardware type
    if [ "$HARDWARE_TYPE" == "auto" ]; then
        HARDWARE_TYPE=$(detect_hardware_type)
        echo "  Auto-detected hardware type: $HARDWARE_TYPE"
    else
        echo "  Hardware type (forced): $HARDWARE_TYPE"
    fi

    # Print detection summary
    print_detection_summary
    echo ""

    # Determine target IP if not specified
    if [ -z "$TARGET_IP" ]; then
        TARGET_IP=$(get_desired_vlan_ip)
        echo "  Auto-assigned target IP: $TARGET_IP"
        echo "  (Based on hardware type and environment-constraints.md)"
    else
        echo "  Target IP (user-specified): $TARGET_IP"
    fi

    echo ""

    # Dry-run check
    if [ "$DRY_RUN" == "true" ]; then
        echo "[DRY-RUN MODE] Would perform the following actions:"
        echo ""
        echo "  Phase A-1  (done)  System detected: $HARDWARE_TYPE"
        echo "  Phase A-2  Configure network: $TARGET_IP (gateway: $GATEWAY, DNS: $DNS_SERVER)"
        echo "  Phase A-3  Install packages:"
        echo "               - Docker (with Debian Trixie workaround if needed)"
        echo "               - Ansible + ansible-lint"
        if [ "$HARDWARE_TYPE" == "proxmox" ]; then
            echo "               - proxmoxer Python library"
            echo "               - Apply Proxmox repository fixes"
        fi
        if [ "$HARDWARE_TYPE" == "pi" ]; then
            echo "               - uv (Python package manager for registry-mcp)"
            echo "               - git-crypt (Phase C prerequisite)"
            echo "               - gh CLI (GitHub CLI — OOBE steps 1, 2, 11, 14)"
            echo "               - Create /mnt/appdata and /mnt/media mount points"
            echo ""
            echo "  NOTE: NFS fstab entries are NOT written here."
            echo "        OOBE step 5 handles NFS configuration interactively."
            echo "        Per ADR-001 §4.2, persistent state must not live on the SD card."
        fi
        echo "  Phase A-4  Generate/verify ED25519 SSH key"
        echo "  Phase A-5  Run validation suite + save log"
        echo "  Phase A-6  Generate hardware fingerprint + inventory stub"
        echo "  → OOBE handoff: oobe_status"
        echo ""
        echo "No changes made. Re-run without --dry-run to execute."
        exit 0
    fi

    # === PHASE A-2: NETWORK CONFIGURATION ===

    if [ "$SKIP_NETWORK" == "false" ]; then
        echo "[PHASE A-2] Network Configuration"
        echo "---"
        configure_network_safe "$TARGET_IP" "$GATEWAY" "$DNS_SERVER"

        # Wait for network to stabilize
        sleep 3
        wait_for_network 15
        echo ""
    else
        echo "[PHASE A-2] Network Configuration (SKIPPED)"
        echo ""
    fi

    # === PHASE A-3: PACKAGE INSTALLATION ===

    echo "[PHASE A-3] Package Installation"
    echo "---"

    # Update package lists
    echo "  [⚙] Updating package lists..."
    sudo apt-get update -qq

    # Install prerequisites
    echo "  [⚙] Installing prerequisites (ca-certificates, curl, gnupg)..."
    sudo apt-get install -y -qq ca-certificates curl gnupg lsb-release

    # --- DOCKER INSTALLATION ---

    if ! command -v docker &>/dev/null; then
        echo "  [⚙] Installing Docker..."

        # Remove existing Docker repo configs
        sudo rm -f /etc/apt/sources.list.d/docker.list
        sudo rm -f /etc/apt/sources.list.d/docker*.list

        # Add Docker GPG key
        sudo mkdir -p /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/debian/gpg | \
            sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes

        # Determine repository codename
        local os_version
        os_version=$(detect_os_version)
        local repo_codename="$os_version"

        # Debian Trixie workaround (use Bookworm repos)
        if is_debian_trixie; then
            echo "  [!] Debian Trixie detected - using Bookworm repos for Docker"
            repo_codename="bookworm"
        fi

        # Add Docker repository
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $repo_codename stable" | \
            sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

        # Install Docker
        sudo apt-get update -qq
        sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

        # Add current user to docker group
        sudo usermod -aG docker "$USER"

        echo "  [✓] Docker installed: $(docker --version)"
        echo "  [!] Docker group added — run 'newgrp docker' or log out/in before using 'docker ps'"
    else
        echo "  [✓] Docker already installed: $(docker --version)"
    fi

    # --- ANSIBLE INSTALLATION ---

    if ! command -v ansible &>/dev/null; then
        echo "  [⚙] Installing Ansible + ansible-lint..."
        sudo apt-get install -y ansible ansible-lint
        echo "  [✓] Ansible installed: $(ansible --version | head -n1)"
    else
        echo "  [✓] Ansible already installed: $(ansible --version | head -n1)"
        # Ensure ansible-lint is present even if ansible was pre-installed
        if ! command -v ansible-lint &>/dev/null; then
            echo "  [⚙] Installing ansible-lint..."
            sudo apt-get install -y ansible-lint
        fi
    fi
    echo "  [✓] ansible-lint: $(ansible-lint --version 2>/dev/null | head -n1 || echo 'installed')"

    # --- PROXMOX-SPECIFIC PACKAGES ---

    if [ "$HARDWARE_TYPE" == "proxmox" ]; then
        echo "  [⚙] Installing Proxmox-specific packages..."

        # Install Python pip if needed
        if ! command -v pip3 &>/dev/null; then
            sudo apt-get install -y python3-pip
        fi

        # Install proxmoxer
        if ! python3 -c "import proxmoxer" 2>/dev/null; then
            echo "  [⚙] Installing proxmoxer Python library..."
            pip3 install proxmoxer --break-system-packages 2>/dev/null || pip3 install proxmoxer
            echo "  [✓] proxmoxer installed"
        else
            echo "  [✓] proxmoxer already installed"
        fi

        # Install jq for Proxmox post-install tasks
        if ! command -v jq &>/dev/null; then
            sudo apt-get install -y jq
        fi

        echo ""
        echo "=== PROXMOX POST-INSTALL CONFIGURATION ==="

        # Run comprehensive Proxmox post-install routine
        # This includes: repository fixes, subscription nag removal, HA management
        run_proxmox_post_install "auto"

        echo "=========================================="
        echo ""
    fi

    # --- PI-SPECIFIC PACKAGES ---

    if [ "$HARDWARE_TYPE" == "pi" ]; then
        echo "  [⚙] Installing Pi / control-plane packages..."

        # uv — Python package manager used by registry-mcp
        # Required before OOBE can run the MCP server
        if ! command -v uv &>/dev/null; then
            echo "  [⚙] Installing uv..."
            curl -LsSf https://astral.sh/uv/install.sh | sh
            export PATH="$HOME/.local/bin:$PATH"
        fi
        echo "  [✓] uv: $(uv --version 2>/dev/null || echo 'installed — reload shell to activate')"

        # git-crypt — required for Phase C (secrets management)
        # OOBE step 13 initialises git-crypt; it must be present beforehand
        if ! command -v git-crypt &>/dev/null; then
            echo "  [⚙] Installing git-crypt..."
            sudo apt-get install -y git-crypt
        fi
        echo "  [✓] git-crypt: $(git-crypt --version 2>/dev/null || echo 'installed')"

        # GitHub CLI (gh) — used by OOBE steps 1, 2, 11, 14
        # (set git provider, create repo, register Actions runner, commit/push)
        if ! command -v gh &>/dev/null; then
            echo "  [⚙] Installing GitHub CLI (gh)..."
            sudo apt-get install -y gh
        fi
        echo "  [✓] gh: $(gh --version 2>/dev/null | head -n1 || echo 'installed')"

        # Create NFS mount point stubs per ADR-001 §4.2
        # NFS fstab entries are written by OOBE step 5 (requires NAS IP — interactive)
        # Local-only fallback: these dirs are used even without NFS
        echo "  [⚙] Creating /mnt/appdata and /mnt/media mount point stubs..."
        sudo mkdir -p /mnt/appdata /mnt/media
        echo "  [✓] Mount points ready (NFS wiring is OOBE step 5)"
    fi

    # --- UTILITY PACKAGES ---

    echo "  [⚙] Installing utility packages..."
    sudo apt-get install -y -qq git vim htop curl wget nfs-common net-tools dnsutils

    echo "  [✓] Package installation complete"
    echo ""

    # === PHASE A-4: SSH KEY MANAGEMENT ===

    echo "[PHASE A-4] SSH Key Management"
    echo "---"

    local ssh_key_path=""

    # Check for existing keys (prefer ED25519)
    if [ -f "$HOME/.ssh/id_ed25519" ]; then
        ssh_key_path="$HOME/.ssh/id_ed25519"
        echo "  [✓] Found existing ED25519 key: $ssh_key_path"
    elif [ -f "$HOME/.ssh/id_rsa" ]; then
        ssh_key_path="$HOME/.ssh/id_rsa"
        echo "  [✓] Found existing RSA key: $ssh_key_path"
        echo "  [!] RSA key found — consider migrating to ED25519 before running OOBE"
    else
        # Generate new ED25519 key
        ssh_key_path="$HOME/.ssh/id_ed25519"
        echo "  [⚙] Generating new ED25519 key pair..."
        ssh-keygen -t ed25519 -f "$ssh_key_path" -N "" -C "$(whoami)@$(hostname)-$(date +%Y%m%d)"
        echo "  [✓] SSH key generated: $ssh_key_path"
    fi

    # Display public key
    # OOBE step 8 (oobe_distribute_ssh_keys) will push this to workload nodes
    echo ""
    echo "  Public key — OOBE will distribute this to workload nodes (step 8):"
    echo "  ---"
    cat "${ssh_key_path}.pub"
    echo "  ---"
    echo ""

    # === PHASE A-5: VALIDATION ===

    if [ "$SKIP_VALIDATION" == "false" ]; then
        echo "[PHASE A-5] System Validation"
        echo "---"

        # Set up log file
        local log_dir="${SCRIPT_DIR}/../ansible/archive/outputs"
        mkdir -p "$log_dir"
        local log_file="${log_dir}/bootstrap-validation-$(hostname)-${TIMESTAMP}.log"

        # Run validation once, capturing to log and stdout simultaneously
        if run_validation_suite 2>&1 | tee "$log_file"; then
            echo ""
            echo "  [✓] All validation checks passed"
        else
            echo ""
            echo "  [!] Validation completed with errors — review above before proceeding"
            echo "  [!] OOBE may still run, but manual intervention may be required"
        fi
        echo "  [✓] Validation log saved: $log_file"
        echo ""
    else
        echo "[PHASE A-5] System Validation (SKIPPED)"
        echo ""
    fi

    # === PHASE A-6: HARDWARE FINGERPRINTING ===

    echo "[PHASE A-6] Hardware Fingerprinting"
    echo "---"

    # Print summary
    print_hardware_summary
    echo ""

    # Validate against standards
    echo "  Checking against environment-constraints.md standards..."
    validate_against_standards || true
    echo ""

    # Save hardware facts
    local facts_dir="${SCRIPT_DIR}/../ansible/archive/outputs"
    local facts_file
    facts_file=$(save_hardware_facts "$facts_dir")
    echo "  [✓] Hardware facts saved: $facts_file"

    # Save JSON if requested
    if [ "$OUTPUT_JSON" == "true" ]; then
        local json_file="${facts_dir}/hardware-facts-$(hostname)-${TIMESTAMP}.json"
        generate_json_output > "$json_file"
        echo "  [✓] JSON output saved: $json_file"
    fi

    # Generate inventory snippet
    # Note: this is a stub — OOBE step 7 (oobe_setup_ansible) writes the real
    # ansible.cfg and inventory once workload node IPs are known
    local inventory_dir="${SCRIPT_DIR}/../ansible/archive/inventory"
    mkdir -p "$inventory_dir"
    local inventory_file
    inventory_file=$(append_to_discovered_inventory "${inventory_dir}/discovered-hosts.yml")
    echo "  [✓] Inventory stub appended: $inventory_file"
    echo "      (OOBE step 7 will write the full ansible.cfg + inventory)"
    echo ""

    # === COMPLETION ===

    echo "======================================="
    echo "BOOTSTRAP COMPLETE — NODE IS OOBE-READY"
    echo "======================================="
    echo ""
    echo "Summary:"
    echo "  Hostname:       $(hostname)"
    echo "  IP Address:     $(get_current_ip)"
    echo "  Hardware Type:  $HARDWARE_TYPE"
    echo "  OS:             $(detect_os_family) $(detect_os_version)"
    echo ""

    echo "Immediate Next Steps:"
    echo "  1. Reconnect SSH if network was reconfigured:"
    echo "       ssh $(whoami)@$(get_current_ip)"
    echo "  2. Activate Docker group (if just installed):"
    echo "       newgrp docker   — or log out/in"
    echo "  3. Verify Docker: docker ps"
    echo "  4. Verify Ansible: ansible --version"
    if [ "$HARDWARE_TYPE" == "pi" ]; then
        echo "  5. Reload shell to pick up uv on PATH:"
        echo "       source \$HOME/.local/bin/env   — or open a new terminal"
        echo "  6. Verify uv: uv --version"
    fi
    echo ""

    echo "OOBE Handoff (ADR-001 §5.1):"
    echo "  The node is ready. Start the MCP server and run:"
    echo "    oobe_status"
    echo "  The OOBE will guide you through steps 1–15:"
    echo "    1.  GitHub provider setup"
    echo "    2.  Homelab repo creation / clone"
    echo "    3.  Register this control plane node"
    echo "    4.  Register workload node(s)"
    echo "    5.  NFS / storage configuration  ← fstab written here"
    echo "    6.  Docker verified on control plane"
    echo "    7.  Ansible + inventory configured  ← ansible.cfg written here"
    echo "    8.  SSH keys distributed to workload nodes"
    echo "    9.  Workload nodes configured"
    echo "    10. Connectivity validated (ansible all -m ping)"
    echo "    11. GitHub Actions runner registered"
    echo "    12. SMTP / notifications configured"
    echo "    13. git-crypt initialised, .env encrypted"
    echo "    14. Initial config committed and pushed"
    echo "    15. First discovery pass — registry populates"
    echo ""

    echo "Files Generated:"
    echo "  - $facts_file"
    [ "$OUTPUT_JSON" == "true" ] && echo "  - ${facts_dir}/hardware-facts-$(hostname)-${TIMESTAMP}.json"
    [ "$SKIP_VALIDATION" == "false" ] && echo "  - ${log_dir}/bootstrap-validation-$(hostname)-${TIMESTAMP}.log"
    echo "  - $inventory_file"
    echo ""
    echo "Documentation:"
    echo "  - ADR-001: docs/ADR-001-Homelab-Control-Plane-Final.docx"
    echo "  - SOP: documentation/SOPs/SOP-002-Initial-Infrastructure-Deployment.md"
    echo "  - Technical Runbook: documentation/TECHNICAL_RUNBOOK.md"
    echo ""
    echo "Have a great day! 🚀"
    echo ""
}

# --- ENTRY POINT ---

# Trap errors
trap 'echo "ERROR: Bootstrap failed at line $LINENO. Check logs for details." >&2' ERR

# Run main workflow
main "$@"

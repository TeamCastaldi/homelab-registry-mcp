#!/bin/bash

# ==============================================================================
# SHARED HELPERS — scripts/install.sh, scripts/bootstrap.sh, and every phase
# script under scripts/phases/{install,bootstrap}/.
# ==============================================================================
# Sourced only, never executed directly. Each phase script is meant to be a
# genuinely standalone, debuggable unit (`bash scripts/phases/install/03-*.sh`
# on its own, not just as a step inside install.sh) — this file is what lets
# every one of them share the same prompt/log/state conventions without
# copy-pasting them.
#
#   source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../lib/common.sh"
#
# (adjust the ../.. depth to wherever the sourcing script actually lives —
# scripts/phases/<group>/NN-*.sh needs ../../lib/common.sh; scripts/*.sh
# itself needs ./lib/common.sh).
# ==============================================================================

# --- OUTPUT ---

info()    { echo "  [✓] $*"; }
action()  { echo "  [⚙] $*"; }
warn()    { echo "  [!] $*"; }
header()  { echo ""; echo "$*"; echo "---"; }
die()     { echo ""; echo "ERROR: $*" >&2; exit 1; }

# --- PROMPTING ---
# Prompt for VAR unless it's already set in the environment (non-interactive
# override). Tests whether the variable is *set* (via `+`), not whether it's
# non-empty (`:-`) — an operator pre-seeding an intentionally blank answer
# (e.g. `GIT_PROVIDER=`) must skip the prompt too, not just one seeded with a
# real value.

prompt() {
    local var_name="$1" prompt_text="$2" default="${3:-}"
    if [ -n "${!var_name+set}" ]; then
        return
    fi
    local input
    if [ -n "$default" ]; then
        read -rp "${prompt_text} [${default}]: " input
        input="${input:-$default}"
    else
        read -rp "${prompt_text}: " input
    fi
    printf -v "$var_name" '%s' "$input"
}

# Same as prompt() but silent (for tokens/keys) and never echoed back.
# Nothing is shown as you type — not even asterisks — so paste/typing
# mistakes are otherwise invisible; print a length-only receipt afterward so
# there's some confirmation without ever putting the value on screen.
prompt_secret() {
    local var_name="$1" prompt_text="$2"
    if [ -n "${!var_name+set}" ]; then
        return
    fi
    local input
    read -rsp "${prompt_text}: " input
    echo ""
    if [ -n "$input" ]; then
        info "Received (${#input} characters, not echoed)"
    else
        warn "No input received — leaving blank"
    fi
    printf -v "$var_name" '%s' "$input"
}

# --- .env READER/WRITER ---
# Set KEY=VALUE in an .env-shaped file, replacing an existing line or
# appending a new one. By default, no-ops when VALUE is empty so unanswered
# prompts leave the file's existing default untouched. Pass allow_empty=true
# to force-blank a key instead (e.g. an optional integration the operator
# deliberately skipped — otherwise its previous non-empty value would
# silently survive). Defaults to ./.env — every phase script `cd`s into
# INSTALL_DIR before calling this, so callers don't need to pass env_file.
set_env() {
    local key="$1" value="$2" allow_empty="${3:-false}" env_file="${4:-.env}"
    if [ -z "$value" ] && [ "$allow_empty" != "true" ]; then
        return
    fi
    local escaped
    escaped=$(printf '%s' "$value" | sed -e 's/\\/\\\\/g' -e 's/&/\\&/g' -e 's/|/\\|/g')
    if grep -q "^${key}=" "$env_file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${escaped}|" "$env_file"
    else
        echo "${key}=${escaped}" >> "$env_file"
    fi
}

# Reads KEY's current value out of an .env-shaped file (empty if absent).
get_env() {
    local key="$1" env_file="${2:-.env}"
    [ -f "$env_file" ] || return 0
    grep "^${key}=" "$env_file" 2>/dev/null | tail -n1 | cut -d= -f2-
}

# --- CROSS-PHASE STATE ---
# install.sh/bootstrap.sh drive their numbered phases (scripts/phases/) as
# separate `bash` processes, not sourced functions — each one is a real,
# independently-runnable script an operator can invoke by hand to debug or
# re-run just one step, which is the whole point of splitting them out. A
# plain shell variable set in one phase's process doesn't survive into the
# next one's, so anything a later phase needs from an earlier one in the
# same run is round-tripped through this small KEY=VALUE file instead —
# the same pattern bootstrap.sh already used for NETWORK_STATE_FILE to carry
# TARGET_IP/etc. from a --skip-network run to a later --network-only one,
# generalized here for every other phase script.
#
# Parsed as plain data (never sourced) — it can hold secrets (GIT_TOKEN,
# ANTHROPIC_API_KEY, ...) collected by earlier phases, so it's created chmod
# 600 and deliberately lives outside any repo checkout (INSTALL_DIR isn't
# even known until the clone phase runs) or version control.
HOMELAB_STATE_DIR="${HOMELAB_STATE_DIR:-${HOME}/.homelab-registry-mcp}"
HOMELAB_STATE_FILE="${HOMELAB_STATE_FILE:-${HOMELAB_STATE_DIR}/install-state.env}"

state_set() {
    local key="$1" value="$2"
    mkdir -p "$HOMELAB_STATE_DIR"
    touch "$HOMELAB_STATE_FILE"
    chmod 600 "$HOMELAB_STATE_FILE"
    local escaped
    escaped=$(printf '%s' "$value" | sed -e 's/\\/\\\\/g' -e 's/&/\\&/g' -e 's/|/\\|/g')
    if grep -q "^${key}=" "$HOMELAB_STATE_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${escaped}|" "$HOMELAB_STATE_FILE"
    else
        echo "${key}=${escaped}" >> "$HOMELAB_STATE_FILE"
    fi
}

# Prints KEY's stored value (or `default` if unset/absent) — does not export.
state_get() {
    local key="$1" default="${2:-}"
    if [ -f "$HOMELAB_STATE_FILE" ]; then
        local line
        line="$(grep "^${key}=" "$HOMELAB_STATE_FILE" 2>/dev/null | tail -n1)"
        if [ -n "$line" ]; then
            printf '%s' "${line#*=}"
            return
        fi
    fi
    printf '%s' "$default"
}

# Wipes all saved state. install.sh/bootstrap.sh call this once at the very
# start of a fresh orchestrated run (so a prior run's answers, or an option
# declined this time, can never silently leak into this one) and again on
# exit via `trap`, since the file can hold secrets that only need to live
# for the duration of one run. Standalone phase invocations deliberately do
# NOT call this on their own — an operator stepping through phases by hand
# relies on state surviving between them.
state_clear() {
    rm -f "$HOMELAB_STATE_FILE" 2>/dev/null || true
}

# --- SHARED CHECKS ---

require_root_or_sudo() {
    # sudo -v prompts for a password if needed (unlike `sudo -n true`, which
    # fails immediately for any user without an already-cached timestamp) —
    # same pattern as reset-node.sh's own require_root_or_sudo.
    if ! sudo -v; then
        die "This script requires sudo. Run as a user with sudo access."
    fi
}

# True when running inside a container (LXC, systemd-nspawn, etc.) rather
# than on bare metal or a VM — see scripts/phases/bootstrap/06-static-ip.sh
# for why this matters.
is_container() {
    systemd-detect-virt --container --quiet 2>/dev/null
}

# Resolves VAR the same way require_install_dir (below) resolves INSTALL_DIR:
# a real (possibly pre-seeded) environment variable wins outright — same as
# every other prompt()-driven value in this project, and correct regardless
# of whether the phase that would normally produce VAR actually ran this
# time — falling back to whatever an earlier phase in this run (or a prior
# standalone one) saved to the state file, then to `default`. Prints the
# result; does not assign it.
resolve_var() {
    local var_name="$1" default="${2:-}"
    if [ -n "${!var_name+set}" ]; then
        printf '%s' "${!var_name}"
    else
        state_get "$var_name" "$default"
    fi
}

# Resolves INSTALL_DIR the same way resolve_var (above) resolves everything
# else: an explicit env var wins, then whatever 01-clone.sh saved to the
# state file, then the documented default — then `cd`s there and confirms
# it's actually a homelab-registry-mcp checkout. Phases call this first thing
# so they work identically whether invoked by install.sh or run by hand.
require_install_dir() {
    INSTALL_DIR="${INSTALL_DIR:-$(state_get INSTALL_DIR "${HOME}/homelab-registry-mcp")}"
    cd "$INSTALL_DIR" 2>/dev/null || die "INSTALL_DIR (${INSTALL_DIR}) does not exist — run scripts/phases/install/01-clone.sh first, or set INSTALL_DIR to an existing checkout."
    [ -f scripts/bootstrap.sh ] || die "scripts/bootstrap.sh not found in ${INSTALL_DIR} — is this the right repo?"
}

# --- BOOTSTRAP DETECTION HELPERS ---
# Shared between bootstrap.sh's own preamble and its phase scripts'
# standalone fallback (used when a phase is run by hand, with nothing saved
# by an orchestrator to read instead). Cheap and side-effect-free enough that
# every caller just re-detects rather than round-tripping through state.

# Sets DOCKER_REPO_OS / DOCKER_REPO_CODENAME (and, as a side effect of
# sourcing /etc/os-release in the caller's own shell, PRETTY_NAME/$ID) —
# supports Debian and Ubuntu only (ADR-001 §3.1), dies clearly on anything
# else rather than guessing.
detect_docker_repo_os() {
    # shellcheck source=/dev/null
    . /etc/os-release
    case "$ID" in
        debian)
            DOCKER_REPO_OS="debian"
            # Docker has not published a repo for Debian releases newer than
            # bookworm (e.g. trixie) as of this writing — bookworm is
            # ABI-compatible and is the documented workaround.
            DOCKER_REPO_CODENAME="bookworm"
            ;;
        ubuntu)
            DOCKER_REPO_OS="ubuntu"
            DOCKER_REPO_CODENAME="$VERSION_CODENAME"
            ;;
        *)
            die "Unsupported OS: ${PRETTY_NAME:-$ID}. This script supports Debian and Ubuntu only (ADR-001 §3.1)."
            ;;
    esac
}

# Sets STATIC_IFACE / NM_CON_NAME: whatever interface currently carries the
# default route, falling back to eth0 if that can't be detected.
detect_static_iface() {
    local detected
    detected="$(ip route show default 2>/dev/null | \
        awk '/^default/ { for (i=1; i<=NF; i++) if ($i == "dev") { print $(i+1); exit } }' || true)"
    STATIC_IFACE="${detected:-eth0}"
    NM_CON_NAME="static-${STATIC_IFACE}"
}

# Sets HARDWARE_LABEL — Raspberry Pi's device-tree model file is the
# reliable signal; anything else is reported by architecture.
detect_hardware_label() {
    if [ -f /proc/device-tree/model ] && grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
        HARDWARE_LABEL="raspberry-pi"
    else
        HARDWARE_LABEL="$(uname -m)"
    fi
}

valid_ip_format() {
    # Dotted-quad shape check only (not full octet-range validation).
    [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

ip_to_int() {
    local a b c d
    IFS=. read -r a b c d <<< "$1"
    echo $(( (a << 24) + (b << 16) + (c << 8) + d ))
}

network_addr() {
    local ip="$1" prefix="$2" mask
    if [ "$prefix" -eq 0 ]; then
        mask=0
    else
        mask=$(( (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF ))
    fi
    echo $(( $(ip_to_int "$ip") & mask ))
}

# Detects the node's live DHCP network config, reconciles it with any
# previously-saved answers in $1 (NETWORK_STATE_FILE — from an earlier
# --skip-network run, or an earlier standalone phase invocation), and
# populates TARGET_IP / TARGET_PREFIX / TARGET_GATEWAY / TARGET_DNS —
# prompting interactively only when there's nothing complete to reuse
# (NETWORK_REUSE_IF_COMPLETE=true) or reuse is not requested at all. Always
# persists the result back to the state file. Requires STATIC_IFACE to
# already be set (detect_static_iface). Shared by bootstrap.sh's own
# preamble and by the hardware-fingerprint/static-ip phase scripts' own
# standalone fallback — every caller of the latter two always passes
# NETWORK_REUSE_IF_COMPLETE=true, since by the time either phase runs there
# is nothing to gain from re-asking a question already answered earlier in
# the same run (or a prior one).
collect_network_config() {
    local network_state_file="$1"

    local DETECTED_GATEWAY DETECTED_IP DETECTED_PREFIX DETECTED_DNS
    DETECTED_GATEWAY="$(ip route show default 2>/dev/null | \
        awk '/^default/ { print $3; exit }' || true)"
    DETECTED_IP="$(ip -o -f inet addr show dev "$STATIC_IFACE" 2>/dev/null | \
        awk '{print $4}' | cut -d/ -f1 | head -n1 || true)"
    DETECTED_PREFIX="$(ip -o -f inet addr show dev "$STATIC_IFACE" 2>/dev/null | \
        awk '{print $4}' | cut -d/ -f2 | head -n1 || true)"
    # IPv4 only: an IPv6 nameserver here (common with systemd-resolved) would
    # sail past this as a detected default, then fail valid_ip_format the
    # moment the operator just presses Enter to accept it.
    DETECTED_DNS="$(awk '/^nameserver/ && $2 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print $2 }' \
        /etc/resolv.conf 2>/dev/null | paste -sd',' - || true)"

    local NETWORK_STATE_COMPLETE=false
    local SAVED_TARGET_IP="" SAVED_TARGET_PREFIX="" SAVED_TARGET_GATEWAY="" SAVED_TARGET_DNS=""
    if [ -f "$network_state_file" ]; then
        # Parsed as plain key=value data, not sourced — the file lives in a
        # writable path, and sourcing it would execute its contents as shell.
        while IFS='=' read -r _state_key _state_value; do
            case "$_state_key" in
                SAVED_TARGET_IP) SAVED_TARGET_IP="$_state_value" ;;
                SAVED_TARGET_PREFIX) SAVED_TARGET_PREFIX="$_state_value" ;;
                SAVED_TARGET_GATEWAY) SAVED_TARGET_GATEWAY="$_state_value" ;;
                SAVED_TARGET_DNS) SAVED_TARGET_DNS="$_state_value" ;;
            esac
        done < "$network_state_file"
        DETECTED_GATEWAY="${SAVED_TARGET_GATEWAY:-$DETECTED_GATEWAY}"
        DETECTED_PREFIX="${SAVED_TARGET_PREFIX:-$DETECTED_PREFIX}"
        DETECTED_DNS="${SAVED_TARGET_DNS:-$DETECTED_DNS}"
        DETECTED_IP="${SAVED_TARGET_IP:-$DETECTED_IP}"
        if [ -n "$SAVED_TARGET_IP" ] && [ -n "$SAVED_TARGET_PREFIX" ] && \
           [ -n "$SAVED_TARGET_GATEWAY" ] && [ -n "$SAVED_TARGET_DNS" ]; then
            NETWORK_STATE_COMPLETE=true
        fi
    fi

    if [ "${NETWORK_REUSE_IF_COMPLETE:-false}" == "true" ] && [ "$NETWORK_STATE_COMPLETE" == "true" ]; then
        TARGET_IP="$SAVED_TARGET_IP"
        TARGET_PREFIX="$SAVED_TARGET_PREFIX"
        TARGET_GATEWAY="$SAVED_TARGET_GATEWAY"
        TARGET_DNS="$SAVED_TARGET_DNS"
        info "Reusing static IP ${TARGET_IP}/${TARGET_PREFIX} collected earlier — skipping the network prompts."
    else
        if [ -f "$network_state_file" ]; then
            info "Reusing network answers saved from an earlier run as the defaults below — press Enter through all four to accept them as-is, or type a new value to change one."
        fi

        read -rp "Enter static IP for ${STATIC_IFACE} [${DETECTED_IP:-${DEFAULT_IP:-192.168.1.200}}]: " TARGET_IP
        TARGET_IP="${TARGET_IP:-${DETECTED_IP:-${DEFAULT_IP:-192.168.1.200}}}"
        valid_ip_format "$TARGET_IP" || die "Invalid IP address: $TARGET_IP"

        read -rp "Subnet prefix length (CIDR bits) [${DETECTED_PREFIX:-${DEFAULT_PREFIX:-24}}]: " TARGET_PREFIX
        TARGET_PREFIX="${TARGET_PREFIX:-${DETECTED_PREFIX:-${DEFAULT_PREFIX:-24}}}"
        [[ "$TARGET_PREFIX" =~ ^[0-9]+$ ]] && [ "$TARGET_PREFIX" -ge 1 ] && [ "$TARGET_PREFIX" -le 32 ] || \
            die "Invalid subnet prefix: $TARGET_PREFIX (expected 1-32)"

        read -rp "Gateway [${DETECTED_GATEWAY:-${GATEWAY:-192.168.1.1}}]: " TARGET_GATEWAY
        TARGET_GATEWAY="${TARGET_GATEWAY:-${DETECTED_GATEWAY:-${GATEWAY:-192.168.1.1}}}"
        valid_ip_format "$TARGET_GATEWAY" || die "Invalid gateway address: $TARGET_GATEWAY"

        read -rp "DNS servers, comma-separated [${DETECTED_DNS:-${DNS_PRIMARY:-192.168.1.1},${DNS_SECONDARY:-8.8.8.8}}]: " TARGET_DNS
        TARGET_DNS="${TARGET_DNS:-${DETECTED_DNS:-${DNS_PRIMARY:-192.168.1.1},${DNS_SECONDARY:-8.8.8.8}}}"
        IFS=',' read -ra _dns_check <<< "$TARGET_DNS"
        for _dns_entry in "${_dns_check[@]}"; do
            valid_ip_format "$_dns_entry" || die "Invalid DNS server address: $_dns_entry"
        done

        # Sanity check: the gateway should live in the same subnet as the
        # chosen static IP.
        if [ "$(network_addr "$TARGET_IP" "$TARGET_PREFIX")" != "$(network_addr "$TARGET_GATEWAY" "$TARGET_PREFIX")" ]; then
            warn "Gateway ${TARGET_GATEWAY} does not appear to be in ${TARGET_IP}/${TARGET_PREFIX}'s subnet — double-check before proceeding."
        fi
    fi

    # Persist so a subsequent invocation (another phase in this run, or a
    # later standalone one) reuses these instead of re-prompting from
    # scratch.
    mkdir -p "$(dirname "$network_state_file")"
    cat > "$network_state_file" << STATE
SAVED_TARGET_IP=${TARGET_IP}
SAVED_TARGET_PREFIX=${TARGET_PREFIX}
SAVED_TARGET_GATEWAY=${TARGET_GATEWAY}
SAVED_TARGET_DNS=${TARGET_DNS}
STATE
}

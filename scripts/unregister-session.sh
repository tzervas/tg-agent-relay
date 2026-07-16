#!/bin/bash
# scripts/unregister-session.sh — remove a session registry entry.
#
# Usage: unregister-session.sh --handle NAME [--sessions-dir DIR]
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"

HANDLE=""
SESSIONS_DIR=""

usage() {
    printf 'usage: %s --handle NAME [--sessions-dir DIR]\n' "$(basename "$0")" >&2
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --handle) HANDLE="${2:-}"; shift 2 ;;
        --sessions-dir) SESSIONS_DIR="${2:-}"; shift 2 ;;
        -h|--help) usage ;;
        *) usage ;;
    esac
done

[[ -n "$HANDLE" ]] || usage

if [[ -z "$SESSIONS_DIR" ]]; then
    if declare -f load_relay_config >/dev/null 2>&1; then
        load_relay_config "$BRIDGE_DIR/relay.toml"
    fi
    SESSIONS_DIR="$(cfg_get '.sessions.dir' "")"
    if [[ -z "$SESSIONS_DIR" ]]; then
        SESSIONS_DIR="${RELAY_SESSIONS_DIR:-$BRIDGE_DIR/.sessions.d}"
    fi
fi
SESSIONS_DIR="${SESSIONS_DIR/#\~/$HOME}"

REG="$SESSIONS_DIR/${HANDLE}.json"
if [[ ! -f "$REG" ]]; then
    printf 'no registration for handle %s\n' "$HANDLE" >&2
    exit 1
fi
rm -f "$REG"
printf 'unregistered handle=%s\n' "$HANDLE"
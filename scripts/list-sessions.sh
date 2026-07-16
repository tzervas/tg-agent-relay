#!/bin/bash
# scripts/list-sessions.sh — list registered @handle sessions.
#
# Usage: list-sessions.sh [--sessions-dir DIR]
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"

SESSIONS_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --sessions-dir) SESSIONS_DIR="${2:-}"; shift 2 ;;
        -h|--help)
            printf 'usage: %s [--sessions-dir DIR]\n' "$(basename "$0")" >&2
            exit 0
            ;;
        *) shift ;;
    esac
done

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

if [[ ! -d "$SESSIONS_DIR" ]]; then
    printf 'no sessions dir (%s)\n' "$SESSIONS_DIR"
    exit 0
fi

shopt -s nullglob
found=0
for f in "$SESSIONS_DIR"/*.json; do
    found=1
    handle="$(jq -r '.handle // empty' "$f" 2>/dev/null)"
    fifo="$(jq -r '.fifo // empty' "$f" 2>/dev/null)"
    pid="$(jq -r '.pid // empty' "$f" 2>/dev/null)"
    at="$(jq -r '.registered_at // empty' "$f" 2>/dev/null)"
    alive="dead"
    if [[ "$pid" =~ ^[0-9]+$ ]] && (( pid > 0 )) && kill -0 "$pid" 2>/dev/null; then
        alive="alive"
    fi
    printf '%s  fifo=%s  pid=%s (%s)  registered=%s\n' "${handle:-?}" "$fifo" "$pid" "$alive" "$at"
done
if (( found == 0 )); then
    printf 'no sessions in %s\n' "$SESSIONS_DIR"
fi
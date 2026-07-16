#!/bin/bash
# scripts/register-session.sh — register a @handle → dedicated FIFO session.
#
# Usage:
#   register-session.sh --handle NAME [--fifo PATH] [--project P] [--pid PID]
#                       [--type grok] [--sessions-dir DIR] [--reclaim]
#
# Writes $SESSIONS_DIR/NAME.json and creates the FIFO. Prints the Monitor command.
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }

HANDLE=""
FIFO=""
PROJECT=""
PID="$$"
TYPE="grok"
SESSIONS_DIR=""
RECLAIM=0

usage() {
    printf 'usage: %s --handle NAME [--fifo PATH] [--project P] [--pid PID] [--type grok] [--sessions-dir DIR] [--reclaim]\n' "$(basename "$0")" >&2
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --handle) HANDLE="${2:-}"; shift 2 ;;
        --fifo) FIFO="${2:-}"; shift 2 ;;
        --project) PROJECT="${2:-}"; shift 2 ;;
        --pid) PID="${2:-}"; shift 2 ;;
        --type) TYPE="${2:-}"; shift 2 ;;
        --sessions-dir) SESSIONS_DIR="${2:-}"; shift 2 ;;
        --reclaim) RECLAIM=1; shift ;;
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
if [[ -f "$REG" ]]; then
    old_pid="$(jq -r '.pid // 0' "$REG" 2>/dev/null || printf '0')"
    if [[ "$old_pid" =~ ^[0-9]+$ ]] && (( old_pid > 0 )); then
        if kill -0 "$old_pid" 2>/dev/null; then
            if (( RECLAIM == 0 )); then
                printf 'error: handle %s already registered (pid %s alive). Use --reclaim to replace.\n' "$HANDLE" "$old_pid" >&2
                exit 1
            fi
            printf 'note: reclaiming handle %s (replacing pid %s)\n' "$HANDLE" "$old_pid" >&2
        fi
    fi
fi

if [[ -z "$FIFO" ]]; then
    FIFO="$BRIDGE_DIR/sessions/${HANDLE}.fifo"
fi
FIFO="${FIFO/#\~/$HOME}"

mkdir -p "$SESSIONS_DIR" "$(dirname "$FIFO")"
if [[ ! -p "$FIFO" ]]; then
    mkfifo "$FIFO"
fi

export PYTHONPATH="$BRIDGE_DIR/lib${PYTHONPATH:+:$PYTHONPATH}"
relay_python -c "
from pathlib import Path
from sessions import write_session_record
p = write_session_record(
    sessions_dir=Path('${SESSIONS_DIR}'),
    handle='${HANDLE}',
    fifo='${FIFO}',
    session_type='${TYPE}',
    project='${PROJECT}',
    pid=int('${PID}'),
)
print(p)
"

printf 'registered handle=%s fifo=%s\n' "$HANDLE" "$FIFO"
printf 'monitor: %s/adapters/backend-fifo-reader.sh %q\n' "$BRIDGE_DIR" "$FIFO"
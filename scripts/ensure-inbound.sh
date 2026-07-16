#!/bin/bash
# scripts/ensure-inbound.sh — Start tg-poll + per-FIFO backend readers (idempotent).
#
# Without a reader open on each inbound FIFO, tg-poll writes fail with ENXIO and
# messages are dropped. This script uses flock + pid files under $BRIDGE_DIR/.run/.
#
# Usage:
#   bash scripts/ensure-inbound.sh [--bridge-dir PATH] [--dry-run]
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0
RESTART_POLL=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bridge-dir) BRIDGE_DIR="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --restart-poll) RESTART_POLL=1; shift ;;
        -h|--help)
            sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) printf 'ensure-inbound.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

BRIDGE_DIR="$(cd "$BRIDGE_DIR" && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/exec-env.sh" ]] && source "$BRIDGE_DIR/lib/exec-env.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"

RUN_DIR="$BRIDGE_DIR/.run"
LOG_DIR="$RUN_DIR/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

pid_alive() {
    local pid="${1:-}"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    (( pid > 0 )) || return 1
    kill -0 "$pid" 2>/dev/null
}

start_fifo_reader() {
    local fifo="$1" key="$2"
    fifo="${fifo/#\~/$HOME}"
    [[ -n "$fifo" ]] || return 0
    local safe_key
    safe_key="$(printf '%s' "$key" | tr -c 'A-Za-z0-9._-' '_')"
    mkdir -p "$(dirname "$fifo")" 2>/dev/null || true
    if [[ ! -p "$fifo" ]]; then
        mkfifo "$fifo" 2>/dev/null || true
    fi
    local lock="$RUN_DIR/fifo-${safe_key}.lock"
    local pidfile="$RUN_DIR/fifo-${safe_key}.pid"
    local logfile="$LOG_DIR/fifo-${safe_key}.log"
    if pid_alive "$(cat "$pidfile" 2>/dev/null || true)"; then
        printf 'ensure-inbound: fifo reader %s already running (pid %s)\n' "$safe_key" "$(cat "$pidfile")"
        return 0
    fi
    rm -f "$pidfile"
    if (( DRY_RUN == 1 )); then
        printf 'ensure-inbound: [dry-run] fifo reader %s fifo=%s\n' "$safe_key" "$fifo"
        return 0
    fi
    if command -v flock >/dev/null 2>&1; then
        flock -n "$lock" bash -c '
            pidf=$1; log=$2; fifo=$3; reader=$4
            echo $$ >"$pidf"
            exec >>"$log" 2>&1
            echo "fifo-reader: $fifo at $(date -Iseconds)"
            exec "$reader" "$fifo"
        ' _ "$pidfile" "$logfile" "$fifo" "$BRIDGE_DIR/adapters/backend-fifo-reader.sh" &
        disown "$!" 2>/dev/null || true
    else
        nohup "$BRIDGE_DIR/adapters/backend-fifo-reader.sh" "$fifo" >>"$logfile" 2>&1 &
        echo $! >"$pidfile"
    fi
    sleep 0.2
    if pid_alive "$(cat "$pidfile" 2>/dev/null || true)"; then
        printf 'ensure-inbound: fifo reader %s (pid %s) fifo=%s\n' "$safe_key" "$(cat "$pidfile")" "$fifo"
    fi
}

# --- tg-poll ---
POLL_LOCK="$RUN_DIR/tg-poll.lock"
POLL_PID="$RUN_DIR/tg-poll.pid"
POLL_LOG="$LOG_DIR/tg-poll.log"
if (( RESTART_POLL == 1 )) && pid_alive "$(cat "$POLL_PID" 2>/dev/null || true)"; then
    old_pid="$(cat "$POLL_PID" 2>/dev/null || true)"
    kill "$old_pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        pid_alive "$old_pid" || break
        sleep 0.2
    done
    if pid_alive "$old_pid"; then
        kill -9 "$old_pid" 2>/dev/null || true
    fi
    rm -f "$POLL_PID"
    printf 'ensure-inbound: restarted tg-poll (was pid %s)\n' "$old_pid"
fi
if pid_alive "$(cat "$POLL_PID" 2>/dev/null || true)"; then
    printf 'ensure-inbound: tg-poll already running (pid %s)\n' "$(cat "$POLL_PID")"
else
    rm -f "$POLL_PID"
    if (( DRY_RUN == 1 )); then
        printf 'ensure-inbound: [dry-run] would start tg-poll\n'
    elif command -v flock >/dev/null 2>&1; then
        flock -n "$POLL_LOCK" bash -c '
            pidf=$1; log=$2; poll=$3
            echo $$ >"$pidf"
            exec >>"$log" 2>&1
            echo "tg-poll: starting at $(date -Iseconds)"
            exec "$poll"
        ' _ "$POLL_PID" "$POLL_LOG" "$BRIDGE_DIR/tg-poll.sh" &
        disown "$!" 2>/dev/null || true
        sleep 0.5
        if pid_alive "$(cat "$POLL_PID" 2>/dev/null || true)"; then
            printf 'ensure-inbound: tg-poll started (pid %s)\n' "$(cat "$POLL_PID")"
        else
            printf 'ensure-inbound: tg-poll launch requested (see %s)\n' "$POLL_LOG"
        fi
    else
        nohup "$BRIDGE_DIR/tg-poll.sh" >>"$POLL_LOG" 2>&1 &
        echo $! >"$POLL_PID"
        printf 'ensure-inbound: tg-poll started without flock (pid %s)\n' "$(cat "$POLL_PID")"
    fi
fi

# --- FIFO readers: registered sessions ---
SESSIONS_DIR="${RELAY_SESSIONS_DIR:-$BRIDGE_DIR/.sessions.d}"
SESSIONS_DIR="${SESSIONS_DIR/#\~/$HOME}"
if [[ -d "$SESSIONS_DIR" ]]; then
    shopt -s nullglob
    for jf in "$SESSIONS_DIR"/*.json; do
        handle="$(jq -r '.handle // empty' "$jf" 2>/dev/null || true)"
        fifo="$(jq -r '.fifo // empty' "$jf" 2>/dev/null || true)"
        [[ -n "$handle" && -n "$fifo" ]] || continue
        start_fifo_reader "$fifo" "$handle"
    done
fi

# --- FIFO readers: static [backends.*] with delivery=fifo ---
if declare -f load_relay_config >/dev/null 2>&1; then
    load_relay_config "$BRIDGE_DIR/relay.toml"
fi
if command -v jq >/dev/null 2>&1 && [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
    while IFS=$'\t' read -r bid fifo delivery; do
        [[ -n "$fifo" ]] || continue
        delivery="${delivery:-fifo}"
        [[ "$delivery" == "fifo" ]] || continue
        start_fifo_reader "$fifo" "backend-${bid}"
    done < <(printf '%s' "$RELAY_CONFIG_JSON" | jq -r '
        (.backends // {}) | to_entries[]
        | [.key, (.value.fifo // ""), (.value.delivery // "fifo")]
        | @tsv')
fi

printf 'ensure-inbound: done (logs in %s)\n' "$LOG_DIR"
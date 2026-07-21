#!/bin/bash
# scripts/ensure-inbound.sh — Start tg-poll + per-FIFO keepalives (idempotent).
#
# Critical design:
#   - tg-poll WRITES to backend/session FIFOs.
#   - Agent harnesses (Grok Build Monitor, Claude Monitor, etc.) must be the
#     processes that READ those FIFOs and deliver lines into the agent.
#   - This script must NOT consume/drain FIFO data into log files — that steals
#     messages from the agent. It only:
#       1) keeps tg-poll running
#       2) holds each unique FIFO open RDWR (no read) so writers never ENXIO
#          when no agent Monitor is attached yet
#
# Usage:
#   bash scripts/ensure-inbound.sh [--bridge-dir PATH] [--dry-run] [--restart-poll]
#   bash scripts/ensure-inbound.sh --kill-stealers
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0
RESTART_POLL=0
KILL_STEALERS=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bridge-dir) BRIDGE_DIR="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --restart-poll) RESTART_POLL=1; shift ;;
        --kill-stealers) KILL_STEALERS=1; shift ;;
        -h|--help)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
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

declare -A SEEN_FIFOS=()

pid_alive() {
    local pid="${1:-}"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    (( pid > 0 )) || return 1
    kill -0 "$pid" 2>/dev/null
}

fifo_key() {
    printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'
}

# Stop log-draining readers owned by *this* script (pidfiles under .run/fifo-*.pid,
# excluding fifo-ka-*). Never pgrep -f (self-matches bash -c wrappers). Never kill
# agent Monitors that run backend-fifo-reader without our stealer pidfiles.
kill_legacy_stealers() {
    local pidf pid
    shopt -s nullglob
    for pidf in "$RUN_DIR"/fifo-*.pid; do
        case "$(basename "$pidf")" in
            fifo-ka-*) continue ;;
        esac
        pid="$(cat "$pidf" 2>/dev/null || true)"
        if ! pid_alive "$pid"; then
            rm -f "$pidf" 2>/dev/null || true
            continue
        fi
        # Confirm this pid's argv actually runs the reader (endswith component)
        if python3 -c '
import sys
pid, suf = sys.argv[1], b"backend-fifo-reader.sh"
try:
    args = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
except OSError:
    sys.exit(1)
sys.exit(0 if any(a.endswith(suf) for a in args if a) else 1)
' "$pid" 2>/dev/null; then
            printf 'ensure-inbound: stopping stealer pid %s (%s)\n' "$pid" "$(basename "$pidf")"
            if (( DRY_RUN == 0 )); then
                kill "$pid" 2>/dev/null || true
                sleep 0.15
                
                rm -f "$pidf"
            fi
        fi
    done
}

# Hold FIFO open RDWR forever without consuming bytes (agent Monitor is the reader).
start_fifo_keepalive() {
    local fifo="$1" label="$2"
    fifo="${fifo/#\~/$HOME}"
    [[ -n "$fifo" ]] || return 0
    [[ "$fifo" == "stdout" ]] && return 0

    mkdir -p "$(dirname "$fifo")" 2>/dev/null || true
    if [[ ! -p "$fifo" ]]; then
        if [[ -e "$fifo" ]]; then
            printf 'ensure-inbound: ERROR %s exists and is not a FIFO\n' "$fifo" >&2
            return 1
        fi
        if ! mkfifo "$fifo" 2>/dev/null; then
            printf 'ensure-inbound: ERROR mkfifo failed for %s\n' "$fifo" >&2
            return 1
        fi
    fi

    local real="$fifo"
    if command -v realpath >/dev/null 2>&1; then
        real="$(realpath -m "$fifo" 2>/dev/null || echo "$fifo")"
    fi
    if [[ -n "${SEEN_FIFOS[$real]:-}" ]]; then
        printf 'ensure-inbound: fifo already covered (%s → %s)\n' "$label" "$real"
        return 0
    fi
    SEEN_FIFOS[$real]=1

    local safe lock pidfile logfile
    safe="$(fifo_key "$real")"
    lock="$RUN_DIR/fifo-ka-${safe}.lock"
    pidfile="$RUN_DIR/fifo-ka-${safe}.pid"
    logfile="$LOG_DIR/fifo-ka-${safe}.log"

    if pid_alive "$(cat "$pidfile" 2>/dev/null || true)"; then
        printf 'ensure-inbound: keepalive %s already running (pid %s) fifo=%s\n' \
            "$label" "$(cat "$pidfile")" "$fifo"
        return 0
    fi
    rm -f "$pidfile"

    if (( DRY_RUN == 1 )); then
        printf 'ensure-inbound: [dry-run] keepalive %s fifo=%s\n' "$label" "$fifo"
        return 0
    fi

    if command -v flock >/dev/null 2>&1; then
        flock -n "$lock" bash -c '
            pidf=$1; log=$2; fifo=$3; label=$4
            echo $$ >"$pidf"
            exec >>"$log" 2>&1
            echo "fifo-keepalive: label=$label fifo=$fifo at $(date -Iseconds)"
            exec 3<>"$fifo" || exit 1
            while true; do sleep 3600; done
        ' _ "$pidfile" "$logfile" "$fifo" "$label" &
        disown "$!" 2>/dev/null || true
    else
        (
            echo $$ >"$pidfile"
            exec >>"$logfile" 2>&1
            exec 3<>"$fifo"
            while true; do sleep 3600; done
        ) &
        disown "$!" 2>/dev/null || true
    fi
    sleep 0.15
    if pid_alive "$(cat "$pidfile" 2>/dev/null || true)"; then
        printf 'ensure-inbound: keepalive %s (pid %s) fifo=%s\n' \
            "$label" "$(cat "$pidfile")" "$fifo"
    else
        printf 'ensure-inbound: WARN keepalive failed for %s (%s)\n' "$label" "$fifo" >&2
    fi
}

kill_legacy_stealers
if (( KILL_STEALERS == 1 )); then
    printf 'ensure-inbound: --kill-stealers complete (also ensuring poll/keepalives)\n'
fi

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

# --- Keepalives: registered sessions ---
SESSIONS_DIR="${RELAY_SESSIONS_DIR:-$BRIDGE_DIR/.sessions.d}"
SESSIONS_DIR="${SESSIONS_DIR/#\~/$HOME}"
if [[ -d "$SESSIONS_DIR" ]]; then
    shopt -s nullglob
    for jf in "$SESSIONS_DIR"/*.json; do
        handle="$(jq -r '.handle // empty' "$jf" 2>/dev/null || true)"
        fifo="$(jq -r '.fifo // empty' "$jf" 2>/dev/null || true)"
        [[ -n "$handle" && -n "$fifo" ]] || continue
        start_fifo_keepalive "$fifo" "session-${handle}"
    done
fi

# --- Keepalives: static [backends.*] with delivery=fifo ---
if declare -f load_relay_config >/dev/null 2>&1; then
    load_relay_config "$BRIDGE_DIR/relay.toml"
fi
if command -v jq >/dev/null 2>&1 && [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
    while IFS=$'\t' read -r bid fifo delivery; do
        [[ -n "$fifo" ]] || continue
        delivery="${delivery:-fifo}"
        [[ "$delivery" == "fifo" ]] || continue
        [[ "$fifo" == "stdout" ]] && continue
        start_fifo_keepalive "$fifo" "backend-${bid}"
    done < <(printf '%s' "$RELAY_CONFIG_JSON" | jq -r '
        (.backends // {}) | to_entries[]
        | [.key, (.value.fifo // ""), (.value.delivery // "fifo")]
        | @tsv')
fi

printf 'ensure-inbound: done — keepalives only (agent Monitors must READ FIFOs)\n'
printf 'ensure-inbound: Grok cabal Monitor command:\n'
printf '  %s/adapters/backend-fifo-reader.sh %s/.grok/telegram-bridge/sessions/cabal.fifo\n' \
    "$BRIDGE_DIR" "${HOME}"
printf 'ensure-inbound: logs in %s\n' "$LOG_DIR"

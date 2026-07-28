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
#
# Safety rails (hang / brick defense):
#   - Only operate on paths ending in `.fifo` — a TSV empty-field collapse used to
#     treat delivery="cmd" as the fifo path and mkfifo a relative `cmd`, replacing
#     an executable with a named pipe (open O_RDONLY then hangs forever).
#   - Open is RDWR (non-blocking on Linux) but still wrapped so a stuck open
#     cannot freeze the parent ensure-inbound process (open runs in the
#     background child; parent only waits briefly for the pidfile).
start_fifo_keepalive() {
    local fifo="$1" label="$2"
    fifo="${fifo/#\~/$HOME}"
    [[ -n "$fifo" ]] || return 0
    [[ "$fifo" == "stdout" ]] && return 0

    # Refuse bare tokens / non-fifo paths (residual TSV false positives).
    case "$fifo" in
        *.fifo) ;;
        *)
            printf 'ensure-inbound: WARN skip non-*.fifo path %s (%s)\n' "$fifo" "$label" >&2
            return 0
            ;;
    esac

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

    # Background child owns the open. Pidfile is written first so the parent
    # can bound its wait and never block on the child's open.
    # Linux RDWR open on a FIFO does not block for a peer; O_RDONLY/O_WRONLY do.
    #
    # Critical: redirect flock's own stdio to the log (not only the inner bash).
    # `flock file cmd` keeps the parent's stdout/stderr open for cmd's lifetime;
    # if ensure-inbound was started under a pipe (pytest, timeout, CI capture),
    # those held FDs prevent EOF and the caller hangs 30–90s until timeout even
    # though this script already printed "done" and exited.
    if command -v flock >/dev/null 2>&1; then
        flock -n "$lock" bash -c '
            pidf=$1; log=$2; fifo=$3; label=$4
            echo $$ >"$pidf"
            echo "fifo-keepalive: label=$label fifo=$fifo at $(date -Iseconds)"
            exec 3<>"$fifo" || { rm -f "$pidf"; exit 1; }
            while true; do sleep 3600; done
        ' _ "$pidfile" "$logfile" "$fifo" "$label" \
            </dev/null >>"$logfile" 2>&1 &
        disown "$!" 2>/dev/null || true
    else
        (
            echo $$ >"$pidfile"
            exec </dev/null >>"$logfile" 2>&1
            exec 3<>"$fifo" || { rm -f "$pidfile"; exit 1; }
            while true; do sleep 3600; done
        ) &
        disown "$!" 2>/dev/null || true
    fi
    # Bounded wait for pidfile (≤ ~2s) — never hang the parent on a stuck child.
    local _i
    for _i in 1 2 3 4 5 6 7 8 9 10; do
        if pid_alive "$(cat "$pidfile" 2>/dev/null || true)"; then
            break
        fi
        sleep 0.2
    done
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

# --- tg-poll (one loop per bot) ---
#
# Telegram's getUpdates cursor is per bot, so each configured bot needs its own
# poll process AND its own state dir (tg_agent_relay.bots.bot_state_dir).  Two
# loops sharing one .offset would advance past each other's updates and eat
# messages silently.  With no [bots.*] table this runs exactly one unnamed loop
# with the legacy pidfile/log names, so single-bot installs are unchanged.
start_poll_for_bot() {
    local bot="${1:-}" label lock pidf log
    if [[ -z "$bot" ]]; then
        label="tg-poll"
    else
        label="tg-poll-${bot}"
    fi
    lock="$RUN_DIR/${label}.lock"
    pidf="$RUN_DIR/${label}.pid"
    log="$LOG_DIR/${label}.log"

    if (( RESTART_POLL == 1 )) && pid_alive "$(cat "$pidf" 2>/dev/null || true)"; then
        local old_pid
        old_pid="$(cat "$pidf" 2>/dev/null || true)"
        kill "$old_pid" 2>/dev/null || true
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            pid_alive "$old_pid" || break
            sleep 0.2
        done
        if pid_alive "$old_pid"; then
            kill -9 "$old_pid" 2>/dev/null || true
        fi
        rm -f "$pidf"
        printf 'ensure-inbound: restarted %s (was pid %s)\n' "$label" "$old_pid"
    fi

    if pid_alive "$(cat "$pidf" 2>/dev/null || true)"; then
        printf 'ensure-inbound: %s already running (pid %s)\n' "$label" "$(cat "$pidf")"
        return 0
    fi

    rm -f "$pidf"
    if (( DRY_RUN == 1 )); then
        printf 'ensure-inbound: [dry-run] would start %s (RELAY_BOT=%s)\n' \
            "$label" "${bot:-default}"
        return 0
    fi

    if command -v flock >/dev/null 2>&1; then
        # Redirect flock's stdio (see start_fifo_keepalive): otherwise pipe
        # capture of ensure-inbound never sees EOF while tg-poll lives.
        RELAY_BOT="$bot" flock -n "$lock" bash -c '
            pidf=$1; log=$2; poll=$3
            echo $$ >"$pidf"
            echo "tg-poll: starting at $(date -Iseconds) bot=${RELAY_BOT:-default}"
            exec "$poll"
        ' _ "$pidf" "$log" "$BRIDGE_DIR/tg-poll.sh" \
            </dev/null >>"$log" 2>&1 &
        disown "$!" 2>/dev/null || true
        sleep 0.5
        if pid_alive "$(cat "$pidf" 2>/dev/null || true)"; then
            printf 'ensure-inbound: %s started (pid %s)\n' "$label" "$(cat "$pidf")"
        else
            printf 'ensure-inbound: %s launch requested (see %s)\n' "$label" "$log"
        fi
    else
        RELAY_BOT="$bot" nohup "$BRIDGE_DIR/tg-poll.sh" </dev/null >>"$log" 2>&1 &
        echo $! >"$pidf"
        printf 'ensure-inbound: %s started without flock (pid %s)\n' "$label" "$(cat "$pidf")"
    fi
}

# Discover configured bots. No [bots.*] → one unnamed (legacy) loop.
if declare -f load_relay_config >/dev/null 2>&1; then
    load_relay_config "$BRIDGE_DIR/relay.toml"
fi
declare -a POLL_BOTS=()
if command -v jq >/dev/null 2>&1 && [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
    while IFS= read -r _bot; do
        [[ -n "$_bot" ]] && POLL_BOTS+=("$_bot")
    done < <(printf '%s' "$RELAY_CONFIG_JSON" | jq -r '(.bots // {}) | keys[]' 2>/dev/null || true)
fi
if (( ${#POLL_BOTS[@]} == 0 )); then
    start_poll_for_bot ""
else
    for _bot in "${POLL_BOTS[@]}"; do
        start_poll_for_bot "$_bot"
    done
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
#
# Field separator is ASCII US (\x1f), NOT tab. Bash treats tab as IFS-whitespace
# even with `IFS=$'\t'`, so empty fields collapse:
#   claude\t\tcmd  →  bid=claude fifo=cmd delivery=""  (delivery defaults to fifo!)
# That false-positive started keepalives on a relative path named "cmd" and
# replaced repo/cwd entrypoints with named pipes (open hangs, ensure appears stuck).
if declare -f load_relay_config >/dev/null 2>&1; then
    load_relay_config "$BRIDGE_DIR/relay.toml"
fi
if command -v jq >/dev/null 2>&1 && [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
    while IFS=$'\x1f' read -r bid fifo delivery; do
        [[ -n "$bid" ]] || continue
        [[ -n "$fifo" ]] || continue
        delivery="${delivery:-fifo}"
        [[ "$delivery" == "fifo" ]] || continue
        [[ "$fifo" == "stdout" ]] && continue
        start_fifo_keepalive "$fifo" "backend-${bid}"
    done < <(printf '%s' "$RELAY_CONFIG_JSON" | jq -r '
        (.backends // {}) | to_entries[]
        | [.key, (.value.fifo // ""), (.value.delivery // "fifo")]
        | join("\u001f")')
fi

printf 'ensure-inbound: done — keepalives only (agent Monitors must READ FIFOs)\n'
printf 'ensure-inbound: Grok cabal Monitor command:\n'
printf '  %s/adapters/backend-fifo-reader.sh %s/.grok/telegram-bridge/sessions/cabal.fifo\n' \
    "$BRIDGE_DIR" "${HOME}"
printf 'ensure-inbound: logs in %s\n' "$LOG_DIR"

# --- Honesty check: keepalive ≠ agent reader ---
# Keepalives make poll open/write succeed; without a Monitor the agent TUI
# never sees inbound traffic. Flag default_backend + common handles loudly.
_check_agent_reader() {
    local label="$1" fifo="$2"
    fifo="${fifo/#\~/$HOME}"
    [[ -n "$fifo" && "$fifo" != "stdout" ]] || return 0
    [[ -p "$fifo" || -e "$fifo" ]] || return 0
    if PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$BRIDGE_DIR" python3 -c '
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tg_agent_relay.poll import fifo_has_agent_reader
sys.exit(0 if fifo_has_agent_reader(sys.argv[2]) else 1)
' "$BRIDGE_DIR" "$fifo" 2>/dev/null; then
        return 0
    fi
    printf 'ensure-inbound: ERROR no agent reader for %s (fifo=%s)\n' "$label" "$fifo" >&2
    printf 'ensure-inbound: ERROR attach Monitor: %s/adapters/backend-fifo-reader.sh %s\n' \
        "$BRIDGE_DIR" "$fifo" >&2
    printf 'ensure-inbound: ERROR keepalive alone is not delivery — poll will emit message_orphaned\n' >&2
    return 1
}

DEFAULT_BACKEND=""
if command -v jq >/dev/null 2>&1 && [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
    DEFAULT_BACKEND="$(printf '%s' "$RELAY_CONFIG_JSON" | jq -r '.routing.default_backend // empty' 2>/dev/null || true)"
fi

# default_backend fifo (if any) — skip non-fifo delivery entirely
if [[ -n "$DEFAULT_BACKEND" ]] && command -v jq >/dev/null 2>&1 && [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
    _db_fifo="$(printf '%s' "$RELAY_CONFIG_JSON" | jq -r --arg b "$DEFAULT_BACKEND" '
        .backends[$b].fifo // empty' 2>/dev/null || true)"
    _db_del="$(printf '%s' "$RELAY_CONFIG_JSON" | jq -r --arg b "$DEFAULT_BACKEND" '
        .backends[$b].delivery // "fifo"' 2>/dev/null || true)"
    if [[ -n "$_db_fifo" && "$_db_del" == "fifo" ]]; then
        _check_agent_reader "default_backend=${DEFAULT_BACKEND}" "$_db_fifo" || true
    fi
fi

# cabal / fleet — only when delivery=fifo (config) or the session registry has
# them. Residual fifo= under delivery=cmd is ignored. Do NOT probe conventional
# ~/.grok/... paths for unconfigured handles: that false-positive-orphans
# cmd/stdout deploys and can stall on live-system FIFOs unrelated to this bridge.
for _handle in cabal fleet; do
    _h_fifo=""
    _h_del=""
    if command -v jq >/dev/null 2>&1 && [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
        _h_fifo="$(printf '%s' "$RELAY_CONFIG_JSON" | jq -r --arg b "$_handle" '
            .backends[$b].fifo // empty' 2>/dev/null || true)"
        _h_del="$(printf '%s' "$RELAY_CONFIG_JSON" | jq -r --arg b "$_handle" '
            .backends[$b].delivery // empty' 2>/dev/null || true)"
    fi
    # Configured non-fifo backend: never honesty-check residual fifo= paths.
    if [[ -n "$_h_del" && "$_h_del" != "fifo" ]]; then
        continue
    fi
    if [[ -z "$_h_fifo" && -f "$SESSIONS_DIR/${_handle}.json" ]]; then
        _h_fifo="$(jq -r '.fifo // empty' "$SESSIONS_DIR/${_handle}.json" 2>/dev/null || true)"
        _h_del="fifo"
    fi
    if [[ -n "$_h_fifo" && "${_h_del:-fifo}" == "fifo" ]]; then
        # Skip duplicate of default_backend already checked
        if [[ -n "${DEFAULT_BACKEND:-}" && "$_handle" == "$DEFAULT_BACKEND" ]]; then
            continue
        fi
        _check_agent_reader "$_handle" "$_h_fifo" || true
    fi
done

printf 'ensure-inbound: doctor:  bash %s/scripts/doctor-inbound.sh --bridge-dir %s\n' \
    "$BRIDGE_DIR" "$BRIDGE_DIR"
printf 'ensure-inbound: health:  bash %s/scripts/inbound-health.sh --bridge-dir %s\n' \
    "$BRIDGE_DIR" "$BRIDGE_DIR"

#!/bin/bash
# handlers/uptime.sh - Relay-handled `/uptime` command: how long the poll
# daemon (tg-poll.sh) has been running, zero model tokens. See
# handlers/README.md for the dispatch contract.
#
# Uptime source (honest, never-silent about which one fired - VR-5): prefers
# the REAL process uptime of the running tg-poll.sh (via `ps`, elapsed time
# since process start - Exact, not an estimate). If no tg-poll.sh process
# can be found (e.g. run standalone, or `ps` unavailable in a minimal
# container), falls back to "earliest metrics.log entry" as a Declared
# proxy for "bridge active since" and says so explicitly - never presents
# an inferred number as if it were the real process uptime.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-common.sh" ]] && source "$BRIDGE_DIR/lib/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }

fmt_duration() {
    local secs="$1" d h m
    d=$(( secs / 86400 ))
    h=$(( (secs % 86400) / 3600 ))
    m=$(( (secs % 3600) / 60 ))
    if (( d > 0 )); then
        printf '%dd %dh %dm' "$d" "$h" "$m"
    elif (( h > 0 )); then
        printf '%dh %dm' "$h" "$m"
    else
        printf '%dm' "$m"
    fi
}

MSG=""
NOW=$(date +%s)

# Prefer a real running tg-poll.sh - oldest matching PID's elapsed seconds.
if command -v pgrep >/dev/null 2>&1 && command -v ps >/dev/null 2>&1; then
    PIDS="$(pgrep -f 'tg-poll\.sh' 2>/dev/null || true)"
    OLDEST_ETIME=""
    for PID in $PIDS; do
        ETIME="$(ps -o etimes= -p "$PID" 2>/dev/null | tr -d ' ')"
        [[ "$ETIME" =~ ^[0-9]+$ ]] || continue
        if [[ -z "$OLDEST_ETIME" || "$ETIME" -gt "$OLDEST_ETIME" ]]; then
            OLDEST_ETIME="$ETIME"
        fi
    done
    if [[ -n "$OLDEST_ETIME" ]]; then
        MSG="🟢 tg-poll.sh uptime: $(fmt_duration "$OLDEST_ETIME") (process elapsed time)"
    fi
fi

# Fallback: earliest line in .metrics.log, labeled as a proxy - never
# silently presented as real process uptime.
if [[ -z "$MSG" ]]; then
    if [[ -s "$BRIDGE_DIR/.metrics.log" ]]; then
        FIRST_TS="$(head -n1 "$BRIDGE_DIR/.metrics.log" 2>/dev/null | cut -f1)"
        if [[ "$FIRST_TS" =~ ^[0-9]+$ ]]; then
            ELAPSED=$(( NOW - FIRST_TS ))
            (( ELAPSED < 0 )) && ELAPSED=0
            MSG="⚠️ no running tg-poll.sh process found — showing a PROXY: earliest .metrics.log entry was $(fmt_duration "$ELAPSED") ago (not real process uptime)"
        fi
    fi
fi

if [[ -z "$MSG" ]]; then
    MSG="❓ uptime unknown — no running tg-poll.sh process found and .metrics.log is empty/absent"
fi

emit_metric "dashboard" "uptime_reply" ""
"$BRIDGE_DIR/relay-notify.sh" --raw "$MSG" >/dev/null 2>&1
exit 0

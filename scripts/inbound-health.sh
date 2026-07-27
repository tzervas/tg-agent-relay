#!/bin/bash
# scripts/inbound-health.sh — Report FIFO keepalive / agent-reader health.
#
# For each backend and registered session FIFO:
#   - keepalive running? (fifo-ka pidfile or /proc cmdline)
#   - agent_reader present? (backend-fifo-reader / tgar-session@)
#   - recent message_orphaned metrics for that backend
#
# Exit codes:
#   0 — all active fifo backends have an agent reader (or no fifo backends)
#   1 — at least one active fifo backend lacks an agent reader
#       (always when orphans found under default check; stricter with
#        REQUIRE_AGENT_READER=1 / --require-agent-reader)
#
# Usage:
#   bash scripts/inbound-health.sh [--bridge-dir PATH] [--require-agent-reader]
#   REQUIRE_AGENT_READER=1 bash scripts/inbound-health.sh
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQUIRE_AGENT_READER="${REQUIRE_AGENT_READER:-0}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bridge-dir) BRIDGE_DIR="${2:-}"; shift 2 ;;
        --require-agent-reader) REQUIRE_AGENT_READER=1; shift ;;
        -h|--help)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) printf 'inbound-health.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

BRIDGE_DIR="$(cd "$BRIDGE_DIR" && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/exec-env.sh" ]] && source "$BRIDGE_DIR/lib/exec-env.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }

RUN_DIR="$BRIDGE_DIR/.run"
METRICS_LOG="${RELAY_METRICS_LOG:-$BRIDGE_DIR/.metrics.log}"
MISSING=0
CHECKED=0

pid_alive() {
    local pid="${1:-}"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    (( pid > 0 )) || return 1
    kill -0 "$pid" 2>/dev/null
}

fifo_key() {
    printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'
}

keepalive_status() {
    local fifo="$1"
    local real safe pidfile pid
    real="$fifo"
    if command -v realpath >/dev/null 2>&1; then
        real="$(realpath -m "$fifo" 2>/dev/null || echo "$fifo")"
    fi
    safe="$(fifo_key "$real")"
    pidfile="$RUN_DIR/fifo-ka-${safe}.pid"
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if pid_alive "$pid"; then
        printf 'yes(pid=%s)' "$pid"
        return 0
    fi
    printf 'no'
    return 1
}

agent_reader_status() {
    local fifo="$1"
    # Prefer installed package; fall back to PYTHONPATH=repo root.
    if RELAY_BRIDGE_DIR="$BRIDGE_DIR" PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$BRIDGE_DIR" \
        relay_python -c '
import os, sys
from pathlib import Path
fifo = sys.argv[1]
root = Path(os.environ.get("RELAY_BRIDGE_DIR") or ".")
sys.path.insert(0, str(root))
from tg_agent_relay.poll import fifo_has_agent_reader
sys.exit(0 if fifo_has_agent_reader(fifo) else 1)
' "$fifo" 2>/dev/null; then
        printf 'yes'
        return 0
    fi
    printf 'no'
    return 1
}

count_orphans() {
    local backend="$1"
    local log="$METRICS_LOG"
    [[ -f "$log" ]] || { printf '0'; return 0; }
    # Count message_orphaned lines for this backend (best-effort, whole log).
    grep -c $'\t'"message_orphaned"$'\t'"backend=${backend} " "$log" 2>/dev/null \
        || grep -c "message_orphaned.*backend=${backend}" "$log" 2>/dev/null \
        || printf '0'
}

last_orphan_detail() {
    local backend="$1"
    local log="$METRICS_LOG"
    [[ -f "$log" ]] || return 0
    grep "message_orphaned" "$log" 2>/dev/null | grep "backend=${backend}" | tail -n 1 || true
}

report_fifo() {
    local kind="$1"  # backend|session
    local id="$2"
    local fifo="$3"
    local delivery="${4:-fifo}"

    fifo="${fifo/#\~/$HOME}"
    [[ -n "$fifo" ]] || return 0
    [[ "$fifo" == "stdout" ]] && return 0
    [[ "$delivery" == "fifo" ]] || return 0

    CHECKED=$((CHECKED + 1))
    local ka ar orphans last
    ka="$(keepalive_status "$fifo" || true)"
    ar="$(agent_reader_status "$fifo" || true)"
    orphans="$(count_orphans "$id" | tr -d '[:space:]')"
    [[ -z "$orphans" ]] && orphans=0
    last="$(last_orphan_detail "$id")"

    printf 'fifo %-8s %-16s keepalive=%-12s agent_reader=%-3s orphans=%s path=%s\n' \
        "$kind" "$id" "$ka" "$ar" "$orphans" "$fifo"
    if [[ -n "$last" ]]; then
        printf '  last_orphan: %s\n' "$last"
    fi

    if [[ "$ar" != "yes" ]]; then
        printf '  WARN: no agent reader — messages may buffer/orphan; attach Monitor:\n' >&2
        printf '    %s/adapters/backend-fifo-reader.sh %s\n' "$BRIDGE_DIR" "$fifo" >&2
        MISSING=$((MISSING + 1))
    fi
}

# --- config / sessions ---
if declare -f load_relay_config >/dev/null 2>&1; then
    load_relay_config "$BRIDGE_DIR/relay.toml"
fi

DEFAULT_BACKEND=""
if command -v jq >/dev/null 2>&1 && [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
    DEFAULT_BACKEND="$(printf '%s' "$RELAY_CONFIG_JSON" | jq -r '.routing.default_backend // empty' 2>/dev/null || true)"
fi

printf 'inbound-health: bridge=%s default_backend=%s require_agent_reader=%s\n' \
    "$BRIDGE_DIR" "${DEFAULT_BACKEND:-<none>}" "$REQUIRE_AGENT_READER"

# Static + session-merged backends from config
if command -v jq >/dev/null 2>&1 && [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
    while IFS=$'\t' read -r bid fifo delivery; do
        [[ -n "$bid" ]] || continue
        report_fifo "backend" "$bid" "$fifo" "$delivery"
    done < <(printf '%s' "$RELAY_CONFIG_JSON" | jq -r '
        (.backends // {}) | to_entries[]
        | select((.value.delivery // "fifo") == "fifo")
        | select((.value.fifo // "") != "" and (.value.fifo // "") != "stdout")
        | [.key, (.value.fifo // ""), (.value.delivery // "fifo")]
        | @tsv')
fi

# Registered sessions (may duplicate backends with same handle — report anyway)
SESSIONS_DIR="${RELAY_SESSIONS_DIR:-$BRIDGE_DIR/.sessions.d}"
SESSIONS_DIR="${SESSIONS_DIR/#\~/$HOME}"
if [[ -d "$SESSIONS_DIR" ]]; then
    shopt -s nullglob
    for jf in "$SESSIONS_DIR"/*.json; do
        handle="$(jq -r '.handle // empty' "$jf" 2>/dev/null || true)"
        fifo="$(jq -r '.fifo // empty' "$jf" 2>/dev/null || true)"
        [[ -n "$handle" && -n "$fifo" ]] || continue
        report_fifo "session" "$handle" "$fifo" "fifo"
    done
fi

if (( CHECKED == 0 )); then
    printf 'inbound-health: no fifo backends/sessions found\n'
fi

if (( MISSING > 0 )); then
    printf 'inbound-health: ERROR %s fifo target(s) lack an agent reader\n' "$MISSING" >&2
    printf 'inbound-health: message_delivered does not mean the agent TUI received the line\n' >&2
    printf 'inbound-health: run ensure-inbound.sh then attach backend-fifo-reader.sh Monitors\n' >&2
    # Always exit 1 when missing readers if require_agent_reader, or by default
    # when any active fifo lacks a reader (honest health gate).
    if [[ "$REQUIRE_AGENT_READER" == "1" ]] || (( MISSING > 0 )); then
        exit 1
    fi
fi

printf 'inbound-health: ok (%s fifo target(s) checked)\n' "$CHECKED"
exit 0

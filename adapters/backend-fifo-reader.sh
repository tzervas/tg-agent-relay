#!/bin/bash
# adapters/backend-fifo-reader.sh - Read a backend's inbound FIFO and print
# lines to stdout (for use as a Monitor / agent event source).
#
# Usage:
#   backend-fifo-reader.sh /path/to/in.fifo [--backend NAME]
#   backend-fifo-reader.sh   # uses $RELAY_FIFO or dies with usage
#
# Each line is already tagged by tg-poll, e.g.:
#   [telegram:backend:grok:project:mycelium] review the parser
#
# Spool replay
# ------------
# tg-poll only writes into this FIFO when an agent reader (this script, or a
# tgar-session@ unit) is actually attached. Anything that arrived while no
# session was up was persisted to .run/spool/<backend>/ instead of being
# written into the keepalive-held kernel buffer where no agent would ever see
# it. So this script drains that spool on startup — replaying, in order, every
# message you missed — and keeps draining on an interval while blocked on the
# FIFO, which covers the window between tg-poll's reader check and this
# process actually attaching.
#
# Env:
#   RELAY_BACKEND          backend id to drain (default: fifo basename sans .fifo)
#   RELAY_SPOOL_POLL_SECS  drain interval in seconds (default 2)
#   RELAY_SPOOL_REPLAY=0   disable spool replay (FIFO-only, legacy behaviour)
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FIFO=""
BACKEND="${RELAY_BACKEND:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --backend) BACKEND="${2:-}"; shift 2 ;;
        -h|--help)
            sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) FIFO="${FIFO:-$1}"; shift ;;
    esac
done

FIFO="${FIFO:-${RELAY_FIFO:-}}"
if [[ -z "$FIFO" ]]; then
    printf 'usage: backend-fifo-reader.sh <fifo-path> [--backend NAME]\n' >&2
    exit 2
fi
FIFO="${FIFO/#\~/$HOME}"

# Backend id defaults to the fifo basename: sessions/fleet.fifo -> fleet.
if [[ -z "$BACKEND" ]]; then
    BACKEND="$(basename "$FIFO")"
    BACKEND="${BACKEND%.fifo}"
fi

if [[ ! -p "$FIFO" ]]; then
    mkdir -p "$(dirname "$FIFO")" 2>/dev/null || true
    mkfifo "$FIFO" 2>/dev/null || true
fi

# Prefer 3.14 via lib/python.sh, but never hard-fail: a missing interpreter
# degrades to the plain FIFO loop rather than dropping the Monitor entirely.
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }

spool_replay_supported() {
    [[ "${RELAY_SPOOL_REPLAY:-1}" != "0" ]] || return 1
    [[ -f "$BRIDGE_DIR/tg_agent_relay/spool.py" ]] || return 1
    relay_python -c 'import sys; sys.exit(0)' >/dev/null 2>&1
}

# Drain every spooled line for this backend to stdout, oldest first.
# Unbuffered so lines reach the agent as they are replayed, not at exit.
drain_spool() {
    PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$BRIDGE_DIR" \
    RELAY_SPOOL_BACKEND="$BACKEND" RELAY_SPOOL_BRIDGE="$BRIDGE_DIR" \
        relay_python -u -c '
import os, sys
sys.path.insert(0, os.environ["RELAY_SPOOL_BRIDGE"])
try:
    from tg_agent_relay.spool import drain
except Exception:
    sys.exit(0)


def emit(line):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


try:
    drain(os.environ["RELAY_SPOOL_BACKEND"], emit, os.environ["RELAY_SPOOL_BRIDGE"])
except Exception:
    # Replay is best-effort: a spool problem must never take down the Monitor.
    sys.exit(0)
' 2>/dev/null
}

REPLAY=0
if spool_replay_supported; then
    REPLAY=1
    # Startup replay: everything that arrived while no session was attached.
    drain_spool
fi

# Background drain keeps replaying while the foreground blocks on the FIFO.
# Line-sized writes are atomic (< PIPE_BUF), so interleaving with FIFO lines
# is safe.
#
# Deliberately NO trap here. The main loop blocks in `read < $FIFO`, and bash
# defers a trap until the current foreground command returns — which never
# happens while no writer is attached. Installing a TERM/INT trap therefore
# makes an idle Monitor unkillable by anything short of SIGKILL. Instead the
# child watches for its parent's death and exits on its own, so cleanup does
# not depend on signal handling in the parent at all.
if (( REPLAY )); then
    (
        PARENT=$PPID
        while kill -0 "$PARENT" 2>/dev/null; do
            sleep "${RELAY_SPOOL_POLL_SECS:-2}"
            kill -0 "$PARENT" 2>/dev/null || break
            drain_spool
        done
    ) &
fi

# Re-open the fifo forever so writer-side closes don't end the reader.
while true; do
    # shellcheck disable=SC2094
    while IFS= read -r line; do
        printf '%s\n' "$line"
    done < "$FIFO"
done

#!/bin/bash
# handlers/stats.sh - Relay-handled `/stats` command: the key metrics
# numbers as plain text (lighter than /dashboard - no image, no bars), zero
# model tokens. See handlers/README.md for the dispatch contract.
#
# Usage: stats.sh "<flattened command text>" - an optional trailing "<N>h"
# window override works the same as handlers/dashboard.sh.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"
if declare -f load_relay_config >/dev/null 2>&1; then
    load_relay_config "$BRIDGE_DIR/relay.toml"
else
    cfg_get() { printf '%s' "$2"; }
fi
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-common.sh" ]] && source "$BRIDGE_DIR/lib/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }

TEXT="${1:-}"

WINDOW_HOURS="$(cfg_get '.dashboard.window_hours' "${TG_DASHBOARD_WINDOW_HOURS:-24}")"
if [[ "$TEXT" =~ ([0-9]+)h?[[:space:]]*$ ]]; then
    WINDOW_HOURS="${BASH_REMATCH[1]}"
fi
[[ "$WINDOW_HOURS" =~ ^[0-9]+$ ]] || WINDOW_HOURS=24

MSG=""
if command -v python3 >/dev/null 2>&1 && [[ -f "$BRIDGE_DIR/lib/metrics_agg.py" ]]; then
    MSG="$(python3 "$BRIDGE_DIR/lib/metrics_agg.py" "$BRIDGE_DIR/.metrics.log" "$WINDOW_HOURS" stats 2>/dev/null)"
fi

if [[ -z "$MSG" ]]; then
    EVENT_COUNT=0
    [[ -f "$BRIDGE_DIR/.metrics.log" ]] && EVENT_COUNT=$(wc -l < "$BRIDGE_DIR/.metrics.log" 2>/dev/null || echo 0)
    MSG="📊 Relay stats (minimal - python3 unavailable)
total metrics events on disk: ${EVENT_COUNT}"
fi

emit_metric "dashboard" "stats_reply" ""
"$BRIDGE_DIR/relay-notify.sh" --raw "$MSG" >/dev/null 2>&1
exit 0

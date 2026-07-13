#!/bin/bash
# handlers/dashboard.sh - Relay-handled `/dashboard` command: renders a
# multi-panel metrics dashboard and sends it to Telegram, ZERO model tokens
# (see handlers/README.md for the dispatch contract this runs under).
#
# Rendering: prefers a matplotlib IMAGE via lib/dashboard_render.py (a clean
# dark-friendly panel figure - header stats / volume-over-time / hook-event
# breakdown / command usage), sent with sendPhoto. Falls back to a crisp
# unicode/text dashboard (lib/metrics_agg.py) sent with sendMessage when
# matplotlib (or python3 itself) is unavailable, or the render errors for
# any reason. NEVER fails silently without sending something - see the
# never-fail contract in lib/dashboard_render.py's header.
#
# Optional TOKEN USAGE panels: if relay.toml's `[usage].enabled = true`,
# this handler refreshes the usage cache (lib/usage_ingest.py) and passes
# it to dashboard_render.py, which appends tokens-by-model/provider/project
# (+ trend) panels below the relay panels. With `[usage]` absent/disabled
# (the default), this is a no-op and /dashboard renders EXACTLY as before -
# see docs/USAGE.md's "Token usage dashboard" section. A dedicated,
# usage-only image is also available via `/usage` (handlers/usage.sh).
#
# Usage (as invoked by tg-poll.sh's dispatch_command):
#   dashboard.sh "<flattened command text>"
# The flattened text may include a window override as a trailing number of
# hours, e.g. "/dashboard 48" or "dashboard 48h" -> last 48 hours. Default
# window: relay.toml [dashboard].window_hours, else TG_DASHBOARD_WINDOW_HOURS,
# else 24.
#
# Test marking: if RELAY_DASHBOARD_TEST_MARK is set (non-empty) in the
# environment, the sent caption/message is prefixed "🔧 dashboard test:" -
# used ONLY by the manual live end-to-end check, never by real usage.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="$BRIDGE_DIR/.env"

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

# Extract an optional trailing "<N>" or "<N>h" window override from the
# command text; falls back to config, then the hardcoded default. Never
# matches anything but a plain integer, so "/dashboard" alone is untouched.
WINDOW_HOURS="$(cfg_get '.dashboard.window_hours' "${TG_DASHBOARD_WINDOW_HOURS:-24}")"
if [[ "$TEXT" =~ ([0-9]+)h?[[:space:]]*$ ]]; then
    WINDOW_HOURS="${BASH_REMATCH[1]}"
fi
[[ "$WINDOW_HOURS" =~ ^[0-9]+$ ]] || WINDOW_HOURS=24

TEST_MARK="${RELAY_DASHBOARD_TEST_MARK:-}"

# Opt-in usage panels (default disabled - see relay.toml.example's [usage]
# table). Only refreshes/passes the cache when explicitly enabled; any
# failure here (bad source, missing python3, ...) just leaves
# USAGE_JSON_ARG empty, and dashboard_render.py renders with no usage
# panels - never blocks the relay-metrics dashboard from sending.
USAGE_JSON_ARG=""
USAGE_DISPLAY_FLAGS=()
if [[ "$(cfg_get '.usage.enabled' "false")" == "true" ]] \
    && command -v python3 >/dev/null 2>&1 \
    && [[ -f "$BRIDGE_DIR/lib/usage_ingest.py" ]]; then
    USAGE_SOURCE="$(cfg_get '.usage.source' "claude-code")"
    USAGE_PROJECTS_DIR="$(cfg_get '.usage.projects_dir' "$HOME/.claude/projects")"
    USAGE_WINDOW="$(cfg_get '.usage.window' "7d")"
    USAGE_CACHE_DIR="$BRIDGE_DIR/.usage"
    mkdir -p "$USAGE_CACHE_DIR" 2>/dev/null
    USAGE_JSON="$USAGE_CACHE_DIR/usage-summary.json"
    python3 "$BRIDGE_DIR/lib/usage_ingest.py" "$USAGE_SOURCE" "$USAGE_PROJECTS_DIR" "$USAGE_WINDOW" "$USAGE_JSON" >/dev/null 2>&1
    [[ -s "$USAGE_JSON" ]] && USAGE_JSON_ARG="$USAGE_JSON"
    [[ "$(cfg_get '.usage.providers' "true")" == "false" ]] && USAGE_DISPLAY_FLAGS+=("--no-providers")
    [[ "$(cfg_get '.usage.models' "true")" == "false" ]] && USAGE_DISPLAY_FLAGS+=("--no-models")
fi

OUT_PNG="$(mktemp -u "${TMPDIR:-/tmp}/relay-dashboard-XXXXXX.png")"

RENDER_OUT=""
if command -v python3 >/dev/null 2>&1 && [[ -f "$BRIDGE_DIR/lib/dashboard_render.py" ]]; then
    RENDER_OUT="$(python3 "$BRIDGE_DIR/lib/dashboard_render.py" "$BRIDGE_DIR/.metrics.log" "$WINDOW_HOURS" "$OUT_PNG" ${USAGE_JSON_ARG:+"$USAGE_JSON_ARG"} "${USAGE_DISPLAY_FLAGS[@]}" 2>/dev/null)"
elif command -v python3 >/dev/null 2>&1 && [[ -f "$BRIDGE_DIR/lib/metrics_agg.py" ]]; then
    # dashboard_render.py missing but metrics_agg.py present (shouldn't
    # happen in a normal checkout) - still get a real text dashboard.
    RENDER_OUT="TEXT
$(python3 "$BRIDGE_DIR/lib/metrics_agg.py" "$BRIDGE_DIR/.metrics.log" "$WINDOW_HOURS" dashboard 2>/dev/null)"
fi

# Ultra-minimal bash-native fallback if python3 itself is unavailable -
# "never fail, always send something" holds even with no python at all.
if [[ -z "$RENDER_OUT" ]]; then
    EVENT_COUNT=0
    [[ -f "$BRIDGE_DIR/.metrics.log" ]] && EVENT_COUNT=$(wc -l < "$BRIDGE_DIR/.metrics.log" 2>/dev/null || echo 0)
    RENDER_OUT="TEXT
📊 Relay Dashboard (minimal - python3 unavailable)
total metrics events on disk: ${EVENT_COUNT}"
fi

MODE_LINE="${RENDER_OUT%%$'\n'*}"
REST="${RENDER_OUT#*$'\n'}"

send_text() {
    local msg="$1"
    [[ -n "$TEST_MARK" ]] && msg=$'🔧 dashboard test:\n'"$msg"
    "$BRIDGE_DIR/relay-notify.sh" --raw "$msg" >/dev/null 2>&1
    emit_metric "dashboard" "render" "text"
}

if [[ "$MODE_LINE" == IMAGE:* && -s "$OUT_PNG" ]]; then
    # No .env / no token -> the same silent no-op every other script in
    # this repo has before setup (see tg-send.sh's header).
    if [[ -f "$CONFIG_FILE" ]]; then
        BOT_TOKEN=""
        ALLOWED_CHAT_ID=""
        # shellcheck disable=SC1090
        source "$CONFIG_FILE"
    fi

    if [[ -n "${BOT_TOKEN:-}" && -n "${ALLOWED_CHAT_ID:-}" ]]; then
        CAPTION="Relay Dashboard — last ${WINDOW_HOURS}h"
        [[ -n "$TEST_MARK" ]] && CAPTION="🔧 dashboard test: ${CAPTION}"
        curl -s -m 20 -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendPhoto" \
            -F "chat_id=${ALLOWED_CHAT_ID}" \
            -F "photo=@${OUT_PNG}" \
            -F "caption=${CAPTION}" \
            >/dev/null 2>&1
        emit_metric "dashboard" "render" "image"
    fi
    # else: no token yet - silent no-op (setup not complete), matching the
    # rest of the repo's "harmless before setup" contract.
else
    send_text "$REST"
fi

rm -f "$OUT_PNG"
exit 0

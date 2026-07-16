#!/bin/bash
# handlers/usage.sh - Relay-handled `/usage` command: an OPT-IN,
# best-effort TOKEN USAGE dashboard (by provider/model/project), ZERO
# model tokens (see handlers/README.md's dispatch contract). Ships ONE
# concrete source adapter today - Claude Code's own local session-
# transcript JSONL (~/.claude/projects/**/*.jsonl by default) - see
# lib/usage_ingest.py's module docstring for the source-adapter contract.
#
# DEFAULT OFF. Nothing here runs unless relay.toml sets:
#   [usage]
#   enabled = true
#
# PRIVACY (this repo is PUBLIC - see docs/USAGE.md's "Token usage
# dashboard" section for the full note): everything this command reads
# and writes stays LOCAL - the configured `projects_dir` (never
# committed - it's session-transcript data, outside this repo entirely)
# and a gitignored aggregate cache under `$BRIDGE_DIR/.usage/` (see
# .gitignore's "Token-usage cache/data" block). The result is sent ONLY
# to the allowlisted Telegram chat this relay already talks to - nothing
# here makes any other network call.
#
# Usage (as invoked by tg-poll.sh's dispatch_command):
#   usage.sh "<flattened command text>"
# Optional trailing window override, same shape as /dashboard's "<N>h":
#   /usage            # relay.toml [usage].window (default 7d)
#   /usage today       # since local midnight
#   /usage 30d         # last 30 days
#   /usage all         # everything the source has
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/exec-env.sh" ]] && source "$BRIDGE_DIR/lib/exec-env.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
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

ENABLED="$(cfg_get '.usage.enabled' "false")"
if [[ "$ENABLED" != "true" ]]; then
    MSG="📈 Token usage tracking is disabled.

Enable it in relay.toml:

  [usage]
  enabled = true

Opt-in and local-only by design — see docs/USAGE.md's \"Token usage dashboard\" section for the privacy note before turning it on."
    emit_metric "usage" "disabled" ""
    "$BRIDGE_DIR/relay-notify.sh" --raw "$MSG" >/dev/null 2>&1
    exit 0
fi

# Default "multi" when unset so Claude + Grok breakdowns appear when both exist.
SOURCE="$(cfg_get '.usage.source' "multi")"
PROJECTS_DIR="$(cfg_get '.usage.projects_dir' "$HOME/.claude/projects")"
WINDOW="$(cfg_get '.usage.window' "7d")"
SHOW_PROVIDERS="$(cfg_get '.usage.providers' "true")"
SHOW_MODELS="$(cfg_get '.usage.models' "true")"

# Chart mode: /usage charts | bar | line | both | allot | share
# (relay.toml usage.charts.default, then RELAY_USAGE_CHART, then both).
CHART_MODE="$(cfg_get '.usage.charts.default' "both")"
CHART_MODE="${RELAY_USAGE_CHART:-$CHART_MODE}"
if [[ "$TEXT" =~ (^|[[:space:]])(charts|chart|bar|line|both|allot|share)([[:space:]]|$) ]]; then
    CHART_MODE="${BASH_REMATCH[2]}"
    TEXT="${TEXT//${BASH_REMATCH[2]}/}"
    TEXT="$(printf '%s' "$TEXT" | sed -E 's/^[[:space:]]+//;s/[[:space:]]+$//')"
fi
[[ "$CHART_MODE" == "charts" || "$CHART_MODE" == "chart" ]] && CHART_MODE="both"

# Optional trailing window override - today/all/lifetime/<N>h|d|w|m|y
# (matches lib/usage_ingest.py's resolve_window() grammar).
if [[ "$TEXT" =~ (today|all|lifetime|[0-9]+[hdwmy])[[:space:]]*$ ]]; then
    WINDOW="${BASH_REMATCH[1]}"
fi

CACHE_DIR="$BRIDGE_DIR/.usage"
mkdir -p "$CACHE_DIR" 2>/dev/null
CACHE_JSON="$CACHE_DIR/usage-summary.json"

ALLOTMENTS_ARG=()
if command -v jq >/dev/null 2>&1; then
    ALLOTMENTS_JSON="$(printf '%s' "${RELAY_CONFIG_JSON:-{}}" | jq -c '.usage.allotments // empty' 2>/dev/null)"
    if [[ -n "$ALLOTMENTS_JSON" && "$ALLOTMENTS_JSON" != "null" && "$ALLOTMENTS_JSON" != "{}" ]]; then
        ALLOTMENTS_FILE="$(mktemp "${TMPDIR:-/tmp}/relay-allotments-XXXXXX.json")"
        printf '%s' "$ALLOTMENTS_JSON" >"$ALLOTMENTS_FILE"
        ALLOTMENTS_ARG=(--allotments "$ALLOTMENTS_FILE")
    fi
fi

if command -v "${RELAY_PYTHON:-python3}" >/dev/null 2>&1 && [[ -f "$BRIDGE_DIR/lib/usage_ingest.py" ]]; then
    relay_python "$BRIDGE_DIR/lib/usage_ingest.py" "$SOURCE" "$PROJECTS_DIR" "$WINDOW" "$CACHE_JSON" \
        "${ALLOTMENTS_ARG[@]}" >/dev/null 2>&1
fi
[[ -n "${ALLOTMENTS_FILE:-}" ]] && rm -f "$ALLOTMENTS_FILE"

DISPLAY_FLAGS=()
[[ "$SHOW_PROVIDERS" == "false" ]] && DISPLAY_FLAGS+=("--no-providers")
[[ "$SHOW_MODELS" == "false" ]] && DISPLAY_FLAGS+=("--no-models")

# Real mktemp (no -u): atomically CREATES the file so the name can never be
# raced/reserved by another process between this call and
# dashboard_render.py actually writing the image (the python side
# overwrites it in place). Previously `mktemp -u` (TOCTOU race - #13
# review LOW).
OUT_PNG="$(mktemp "${TMPDIR:-/tmp}/relay-usage-XXXXXX.png")"

RENDER_ERR=""
RENDER_OUT=""
if command -v "${RELAY_PYTHON:-python3}" >/dev/null 2>&1 && [[ -f "$BRIDGE_DIR/lib/dashboard_render.py" ]]; then
    RENDER_ERRFILE="$(mktemp "${TMPDIR:-/tmp}/relay-usage-render-err-XXXXXX")"
    RENDER_OUT="$(relay_python "$BRIDGE_DIR/lib/dashboard_render.py" --usage-only "$CACHE_JSON" "$OUT_PNG" \
        --chart "$CHART_MODE" "${DISPLAY_FLAGS[@]}" 2>"$RENDER_ERRFILE")" || true
    RENDER_ERR="$(head -n 1 "$RENDER_ERRFILE" 2>/dev/null || true)"
    rm -f "$RENDER_ERRFILE"
fi

# Rich text breakdown (providers, harness sources, optional quotas) — always
# attempted when the cache exists; independent of matplotlib.
USAGE_TEXT=""
if command -v "${RELAY_PYTHON:-python3}" >/dev/null 2>&1 \
    && [[ -f "$BRIDGE_DIR/lib/usage_ingest.py" && -s "$CACHE_JSON" ]]; then
    TEXT_FLAGS=()
    [[ "$SHOW_PROVIDERS" == "false" ]] && TEXT_FLAGS+=(--no-providers)
    [[ "$SHOW_MODELS" == "false" ]] && TEXT_FLAGS+=(--no-models)
    USAGE_TEXT="$(relay_python "$BRIDGE_DIR/lib/usage_ingest.py" --telegram-text "$CACHE_JSON" \
        "${TEXT_FLAGS[@]}" 2>/dev/null)"
fi

# Never fail to send SOMETHING, even with no python3 at all (same
# never-fail contract as handlers/dashboard.sh).
HAS_MPL=0
if relay_python -c "import matplotlib" >/dev/null 2>&1; then
    HAS_MPL=1
fi

if [[ -z "$RENDER_OUT" ]]; then
    if [[ -n "$USAGE_TEXT" ]]; then
        RENDER_OUT="TEXT
${USAGE_TEXT}"
    elif [[ "$HAS_MPL" -eq 0 ]]; then
        RENDER_OUT="TEXT
📈 Token usage (text only) — charts need matplotlib; redeploy with dashboard extra: uv pip install -e \".[dashboard]\""
    else
        chart_hint=""
        if [[ -n "$RENDER_ERR" ]]; then
            chart_hint=" (${RENDER_ERR})"
        fi
        RENDER_OUT="TEXT
📈 Token usage (text only) — chart render failed${chart_hint}"
    fi
fi

MODE_LINE="${RENDER_OUT%%$'\n'*}"
REST="${RENDER_OUT#*$'\n'}"
PHOTO_CAPTION="Token Usage — ${WINDOW}"
if [[ "$REST" == CAPTION:* ]]; then
    PHOTO_CAPTION="${REST%%$'\n'*}"
    PHOTO_CAPTION="${PHOTO_CAPTION#CAPTION:}"
    REST="${REST#*$'\n'}"
fi

send_text() {
    local msg="$1"
    "$BRIDGE_DIR/relay-notify.sh" --raw "$msg" >/dev/null 2>&1
    emit_metric "usage" "render" "text"
}

if [[ -n "$USAGE_TEXT" ]]; then
    send_text "$USAGE_TEXT"
fi

if [[ "$MODE_LINE" == IMAGE:* && -s "$OUT_PNG" ]]; then
    # No .env / no token -> the same silent no-op every other script in
    # this repo has before setup (see tg-send.sh's header).
    if [[ -f "$CONFIG_FILE" ]]; then
        BOT_TOKEN=""
        ALLOWED_CHAT_ID=""
        # shellcheck disable=SC1090
        source "$CONFIG_FILE"
    fi
    # Prefer originating chat from tg-poll (multi-room) over legacy default.
    SEND_CHAT="${RELAY_CHAT_ID:-${ALLOWED_CHAT_ID:-}}"

    if [[ -n "${BOT_TOKEN:-}" && -n "${SEND_CHAT:-}" ]]; then
        CAPTION="$PHOTO_CAPTION"
        curl -s -m 20 -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendPhoto" \
            -F "chat_id=${SEND_CHAT}" \
            -F "photo=@${OUT_PNG}" \
            --form-string "caption=${CAPTION}" \
            >/dev/null 2>&1
        emit_metric "usage" "render" "image"
    fi
    # else: no token yet - silent no-op (setup not complete), matching the
    # rest of the repo's "harmless before setup" contract.
elif [[ -z "$USAGE_TEXT" ]]; then
    send_text "$REST"
fi

rm -f "$OUT_PNG"
exit 0

#!/bin/bash
# adapters/grok.sh - Grok Build provider entry (thin shell → providers/grok).
#
# Full hook catalog + formatting lives in providers/grok/hooks.py (all 14
# documented Grok Build events + Cursor aliases). This script:
#   1. Loads relay.toml → temp JSON config overrides for [grok.*]
#   2. Runs lib/provider_hook.py grok on stdin
#   3. Sends OK summaries via relay-notify (TG_SEND_SOURCE=hook)
#
# Wire with: install-grok-hooks.sh → ~/.grok/hooks/tg-agent-relay.json
# Always exits 0 (notify-only; never block PreToolUse).
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/relay-config.sh"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/relay-common.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/routing.sh" ]] && source "$BRIDGE_DIR/lib/routing.sh"

load_relay_config "$BRIDGE_DIR/relay.toml"

PAYLOAD="$(cat 2>/dev/null || true)"
[[ -z "$PAYLOAD" ]] && exit 0

# Prefer 3.14 via lib/python.sh
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }

if ! command -v "${RELAY_PYTHON:-python3}" >/dev/null 2>&1 || [[ ! -f "$BRIDGE_DIR/lib/provider_hook.py" ]]; then
    emit_metric "hook" "grok_skip" "python3_or_provider_hook_missing"
    exit 0
fi

# Export [grok] section as JSON for provider_hook (enabled/prefix/format).
CFG_JSON="$(mktemp)"
if command -v jq >/dev/null 2>&1 && [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
    printf '%s' "$RELAY_CONFIG_JSON" | jq -c '{grok: (.grok // {})}' > "$CFG_JSON" 2>/dev/null \
        || printf '%s\n' '{}' > "$CFG_JSON"
else
    printf '%s\n' '{}' > "$CFG_JSON"
fi

OUT="$(printf '%s' "$PAYLOAD" | relay_python "$BRIDGE_DIR/lib/provider_hook.py" grok \
    --config-json "$CFG_JSON" --emit-meta 2>/dev/null)" || OUT=""
rm -f "$CFG_JSON"

LINE1="$(printf '%s\n' "$OUT" | head -1)"
META="$(printf '%s\n' "$OUT" | sed -n 's/^META://p' | head -1)"

case "$LINE1" in
    OK:*)
        SUMMARY="${LINE1#OK:}"
        emit_metric "hook" "grok_event" "${META:-ok}"
        export RELAY_BACKEND="${RELAY_BACKEND:-grok}"
        # Project from cwd in META or payload
        CWD="$(printf '%s' "$PAYLOAD" | jq -r '.cwd // .workspaceRoot // empty' 2>/dev/null)"
        [[ -z "$CWD" || "$CWD" == "null" ]] && CWD="${GROK_WORKSPACE_ROOT:-${CLAUDE_PROJECT_DIR:-}}"
        if [[ -z "${RELAY_PROJECT:-}" ]] && declare -f project_from_cwd >/dev/null 2>&1; then
            RELAY_PROJECT="$(project_from_cwd "${CWD:-}")"
            [[ -n "$RELAY_PROJECT" ]] && export RELAY_PROJECT
        fi
        if [[ -n "$SUMMARY" ]]; then
            _GROK_EV="$(printf '%s' "$PAYLOAD" | jq -r '.hookEventName // .hook_event_name // empty' 2>/dev/null)"
            [[ -z "$_GROK_EV" || "$_GROK_EV" == "null" ]] && _GROK_EV="${GROK_HOOK_EVENT:-}"
            export RELAY_HOOK_EVENT="$_GROK_EV"
            [[ -n "$CWD" && -d "$CWD" ]] && export RELAY_CWD="$CWD"
            TG_SEND_SOURCE=hook "$BRIDGE_DIR/relay-notify.sh" --raw "$SUMMARY" >/dev/null 2>&1
        fi
        ;;
    SKIP:*)
        emit_metric "hook" "grok_skip" "${LINE1#SKIP:}"
        ;;
    *)
        emit_metric "hook" "grok_skip" "empty_or_error"
        ;;
esac

exit 0

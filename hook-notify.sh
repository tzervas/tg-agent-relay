#!/bin/bash
# hook-notify.sh - Smart hook entry (Claude Code + Grok Build + Cursor).
#
# Wired into ~/.claude/settings.json (and loaded by Grok's Claude-compat
# hooks scanner). Grok also installs ~/.grok/hooks/tg-agent-relay.json →
# hook-notify-grok.sh, but when only Claude settings are scanned, Grok
# events still arrive HERE. We must not treat them as Claude Code.
#
# Dispatch:
#   - GROK_* env or payload.hookEventName  → adapters/grok.sh
#   - otherwise                            → adapters/claude-code.sh
#
# Always exits 0. Reads full stdin once, re-feeds the chosen adapter.
# Note: cannot use `exec` after a pipe (pipeline doesn't replace this shell).
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD="$(cat 2>/dev/null || true)"
[[ -z "$PAYLOAD" ]] && exit 0

IS_GROK=0
if [[ -n "${GROK_HOOK_EVENT:-}" || -n "${GROK_SESSION_ID:-}" || -n "${GROK_WORKSPACE_ROOT:-}" ]]; then
    IS_GROK=1
elif command -v jq >/dev/null 2>&1; then
    if printf '%s' "$PAYLOAD" | jq -e '(.hookEventName // empty) | length > 0' >/dev/null 2>&1; then
        IS_GROK=1
    fi
elif [[ "$PAYLOAD" == *'"hookEventName"'* ]]; then
    IS_GROK=1
fi

if (( IS_GROK == 1 )) && [[ -x "$BRIDGE_DIR/adapters/grok.sh" || -f "$BRIDGE_DIR/adapters/grok.sh" ]]; then
    printf '%s' "$PAYLOAD" | bash "$BRIDGE_DIR/adapters/grok.sh"
    exit 0
fi

printf '%s' "$PAYLOAD" | bash "$BRIDGE_DIR/adapters/claude-code.sh"
exit 0

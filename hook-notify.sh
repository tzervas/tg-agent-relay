#!/bin/bash
# hook-notify.sh - Claude Code hook shim.
#
# Wired into ~/.claude/settings.json under hooks.SubagentStop / hooks.Notification.
# Reads the hook's JSON payload on stdin, builds a short human-readable summary,
# and forwards it to tg-send.sh (which itself no-ops silently if no token is set).
#
# Always exits 0: these two hook events are advisory for SubagentStop (non-zero
# there would BLOCK the subagent from stopping) and fire-and-forget for
# Notification - this script must never affect Claude Code's own control flow.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PAYLOAD="$(cat 2>/dev/null || true)"
[[ -z "$PAYLOAD" ]] && exit 0

EVENT=$(printf '%s' "$PAYLOAD" | jq -r '.hook_event_name // "unknown"' 2>/dev/null)

SUMMARY=""
case "$EVENT" in
    SubagentStop)
        AGENT_TYPE=$(printf '%s' "$PAYLOAD" | jq -r '.agent_type // "agent"' 2>/dev/null)
        STOP_REASON=$(printf '%s' "$PAYLOAD" | jq -r '.stop_reason // "done"' 2>/dev/null)
        LAST_MSG=$(printf '%s' "$PAYLOAD" | jq -r '.last_assistant_message // ""' 2>/dev/null)
        SNIPPET="${LAST_MSG:0:160}"
        SUMMARY="🤖 ${AGENT_TYPE} finished (${STOP_REASON})"
        [[ -n "$SNIPPET" ]] && SUMMARY="${SUMMARY}: ${SNIPPET}"
        ;;
    Notification)
        NTYPE=$(printf '%s' "$PAYLOAD" | jq -r '.notification_type // "notice"' 2>/dev/null)
        NMSG=$(printf '%s' "$PAYLOAD" | jq -r '.message // ""' 2>/dev/null)
        SUMMARY="🔔 ${NTYPE}: ${NMSG:0:200}"
        ;;
    *)
        SUMMARY="ℹ️ Claude Code event: ${EVENT}"
        ;;
esac

[[ -n "$SUMMARY" ]] && "$BRIDGE_DIR/tg-send.sh" "$SUMMARY" >/dev/null 2>&1

exit 0

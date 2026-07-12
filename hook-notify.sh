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
#
# Field names verified against the official Claude Code hooks reference
# (code.claude.com/docs/en/hooks.md, checked 2026-07-12):
#   SubagentStop:  hook_event_name, agent_type, agent_id, last_assistant_message.
#                  There is NO `stop_reason` field - it never existed in the
#                  real payload, so every message silently rendered "(done)".
#                  Dropped rather than kept as a fake-looking default.
#   Notification:  hook_event_name, notification_type, message.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Telegram's sendMessage cap is 4096 chars; a status line only needs a few
# hundred to be genuinely useful. This replaces the old 160/200-char cap,
# which cut a real multi-sentence agent summary down to a fragment ending
# mid-word with no indication anything was cut.
SNIPPET_MAX=400

# Collapse a field's whitespace/newlines into a single-line summary, trim
# leading/trailing space, and cap length with a trailing "..." marker so a
# message is never silently cut with no sign that text is missing.
oneline_snip() {
    local text="$1" max="$2"
    text="$(printf '%s' "$text" | tr '\n\r\t' ' ' | tr -s ' ')"
    text="${text#"${text%%[![:space:]]*}"}"
    text="${text%"${text##*[![:space:]]}"}"
    if (( ${#text} > max )); then
        printf '%s...' "${text:0:max}"
    else
        printf '%s' "$text"
    fi
}

PAYLOAD="$(cat 2>/dev/null || true)"
[[ -z "$PAYLOAD" ]] && exit 0

EVENT=$(printf '%s' "$PAYLOAD" | jq -r '.hook_event_name // "unknown"' 2>/dev/null)

SUMMARY=""
case "$EVENT" in
    SubagentStop)
        AGENT_TYPE=$(printf '%s' "$PAYLOAD" | jq -r '.agent_type // "agent"' 2>/dev/null)
        LAST_MSG=$(printf '%s' "$PAYLOAD" | jq -r '.last_assistant_message // ""' 2>/dev/null)
        SNIPPET="$(oneline_snip "$LAST_MSG" "$SNIPPET_MAX")"
        SUMMARY="✅ ${AGENT_TYPE} finished"
        [[ -n "$SNIPPET" ]] && SUMMARY="${SUMMARY} — ${SNIPPET}"
        ;;
    Notification)
        NTYPE=$(printf '%s' "$PAYLOAD" | jq -r '.notification_type // "notice"' 2>/dev/null)
        NMSG=$(printf '%s' "$PAYLOAD" | jq -r '.message // ""' 2>/dev/null)
        SNIPPET="$(oneline_snip "$NMSG" "$SNIPPET_MAX")"
        SUMMARY="🔔 ${NTYPE}"
        [[ -n "$SNIPPET" ]] && SUMMARY="${SUMMARY}: ${SNIPPET}"
        ;;
    *)
        SUMMARY="ℹ️ Claude Code event: ${EVENT}"
        ;;
esac

[[ -n "$SUMMARY" ]] && "$BRIDGE_DIR/tg-send.sh" "$SUMMARY" >/dev/null 2>&1

exit 0

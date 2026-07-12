#!/bin/bash
# hook-notify.sh - Claude Code hook shim.
#
# Wired into ~/.claude/settings.json under hooks.SubagentStop / hooks.Notification.
# Reads the hook's JSON payload on stdin, builds a readable summary, and forwards
# the FULL text to tg-send.sh (which itself no-ops silently if no token is set).
# tg-send.sh auto-paginates anything over its page size ([k/n]-prefixed, sent in
# order) - so a hook-triggered status ping arrives complete, not truncated, the
# same as a long manual message would.
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

# tg-send.sh paginates at this many chars/page (must match its own default -
# override both consistently via TG_PAGE_SIZE if you change one).
PAGE_SIZE="${TG_PAGE_SIZE:-3500}"
[[ "$PAGE_SIZE" =~ ^[0-9]+$ ]] || PAGE_SIZE=3500
# A runaway hook event (e.g. a pathological agent dump) is still bounded -
# not to keep it short, but so one event can't flood the chat with dozens of
# pages. Default 6 pages (~21k chars) is generous for any real status update.
MAX_PAGES="${TG_HOOK_MAX_PAGES:-6}"
[[ "$MAX_PAGES" =~ ^[0-9]+$ ]] || MAX_PAGES=6
MAX_CHARS=$(( PAGE_SIZE * MAX_PAGES ))

# Collapse a field's whitespace/newlines into a single-line, readable summary
# and trim leading/trailing space. No length cap here - the full text is
# forwarded to tg-send.sh for pagination; see cap_if_huge() for the one
# remaining (much larger, never-silent) outlier cap.
oneline() {
    local text="$1"
    text="$(printf '%s' "$text" | tr '\n\r\t' ' ' | tr -s ' ')"
    text="${text#"${text%%[![:space:]]*}"}"
    text="${text%"${text##*[![:space:]]}"}"
    printf '%s' "$text"
}

# Bound only an extreme outlier (many pages' worth). Never a silent/mid-word
# cut: truncates at a char boundary and appends a marker stating how many
# pages were dropped, rather than fabricating a shortened message with no
# sign anything is missing. Because oneline() always yields a single line
# with no newlines, tg-send.sh's real (line-boundary) pagination degrades to
# its hard-split fallback here, splitting every exactly PAGE_SIZE chars - so
# this page-count estimate is exact, not approximate.
cap_if_huge() {
    local text="$1"
    if (( ${#text} <= MAX_CHARS )); then
        printf '%s' "$text"
        return
    fi
    local kept="${text:0:MAX_CHARS}"
    local omitted_chars=$(( ${#text} - MAX_CHARS ))
    local omitted_pages=$(( (omitted_chars + PAGE_SIZE - 1) / PAGE_SIZE ))
    printf '%s\n\n[+%s more pages omitted]' "$kept" "$omitted_pages"
}

PAYLOAD="$(cat 2>/dev/null || true)"
[[ -z "$PAYLOAD" ]] && exit 0

EVENT=$(printf '%s' "$PAYLOAD" | jq -r '.hook_event_name // "unknown"' 2>/dev/null)

SUMMARY=""
case "$EVENT" in
    SubagentStop)
        AGENT_TYPE=$(printf '%s' "$PAYLOAD" | jq -r '.agent_type // "agent"' 2>/dev/null)
        LAST_MSG=$(printf '%s' "$PAYLOAD" | jq -r '.last_assistant_message // ""' 2>/dev/null)
        SNIPPET="$(oneline "$LAST_MSG")"
        SUMMARY="✅ ${AGENT_TYPE} finished"
        [[ -n "$SNIPPET" ]] && SUMMARY="${SUMMARY} — ${SNIPPET}"
        ;;
    Notification)
        NTYPE=$(printf '%s' "$PAYLOAD" | jq -r '.notification_type // "notice"' 2>/dev/null)
        NMSG=$(printf '%s' "$PAYLOAD" | jq -r '.message // ""' 2>/dev/null)
        SNIPPET="$(oneline "$NMSG")"
        SUMMARY="🔔 ${NTYPE}"
        [[ -n "$SNIPPET" ]] && SUMMARY="${SUMMARY}: ${SNIPPET}"
        ;;
    *)
        SUMMARY="ℹ️ Claude Code event: ${EVENT}"
        ;;
esac

SUMMARY="$(cap_if_huge "$SUMMARY")"

[[ -n "$SUMMARY" ]] && "$BRIDGE_DIR/tg-send.sh" "$SUMMARY" >/dev/null 2>&1

exit 0

#!/bin/bash
# adapters/claude-code.sh - Claude Code hook-JSON adapter.
#
# Reads a Claude Code hook payload on stdin, builds a readable per-event
# summary, and hands it to relay-notify.sh (the harness-neutral core) for
# delivery. This is the logic that used to live directly in hook-notify.sh
# (see git history) - it moved here so hook-notify.sh could become a thin,
# backward-compatible shim (CLAUDE-Code-specific parsing is now ONE adapter
# among possibly many; see adapters/README.md to write another).
#
# Wired into ~/.claude/settings.json today under hooks.SubagentStop /
# hooks.Notification (via the hook-notify.sh shim). Always exits 0: these
# events are advisory (SubagentStop) or fire-and-forget (Notification) -
# this script must never affect Claude Code's own control flow, no matter
# which event fires.
#
# Full documented Claude Code hook event set (verified against
# code.claude.com/docs/en/hooks.md, checked 2026-07-12): SessionStart,
# Setup, UserPromptSubmit, UserPromptExpansion, PreToolUse,
# PermissionRequest, PermissionDenied, PostToolUse, PostToolUseFailure,
# PostToolBatch, Notification, MessageDisplay, SubagentStart, SubagentStop,
# TaskCreated, TaskCompleted, Stop, StopFailure, TeammateIdle,
# InstructionsLoaded, ConfigChange, CwdChanged, FileChanged,
# WorktreeCreate, WorktreeRemove, PreCompact, PostCompact, Elicitation,
# ElicitationResult, SessionEnd. Only SubagentStop/Notification are
# actually wired into settings.json's hooks today (that wiring is
# untouched by this change - see README's "live bridge" note); the
# handling below covers the subset "that makes sense" as a status ping
# (Stop, SubagentStart, PreToolUse/PostToolUse[Failure], SessionStart/End,
# PreCompact, StopFailure) so wiring one in later needs no code change -
# only a settings.json hook entry (+ optionally a relay.toml
# [claude_code.<Event>] override).
#
# Field names per event, from the same reference:
#   SubagentStop:  agent_type, last_assistant_message, stop_hook_active.
#   Notification:  notification_type, message.
#   Stop:          last_assistant_message, stop_hook_active.
#   SubagentStart: agent_type.
#   PreToolUse:    tool_name, tool_input.
#   PostToolUse:   tool_name, tool_input, tool_output.
#   PostToolUseFailure: tool_name, tool_input, error.
#   StopFailure:   error_type.
#   SessionStart:  source, model, session_title.
#   SessionEnd:    end_reason.
#   PreCompact:    compaction_trigger.
# All events also carry hook_event_name (used to dispatch below).
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/relay-config.sh"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/relay-common.sh"

load_relay_config "$BRIDGE_DIR/relay.toml"

PAYLOAD="$(cat 2>/dev/null || true)"
[[ -z "$PAYLOAD" ]] && exit 0

EVENT=$(printf '%s' "$PAYLOAD" | jq -r '.hook_event_name // "unknown"' 2>/dev/null)

# event_enabled <EventName> -> "true"/"false" (relay.toml
# [claude_code.<EventName>].enabled, default "true" - matching the
# original hook-notify.sh's unconditional-emit behavior for every event it
# was ever actually invoked with, since no "enabled" concept existed
# before this change).
event_enabled() {
    cfg_get ".claude_code.\"$1\".enabled" "true"
}

# event_prefix <EventName> <built-in-default-prefix>
event_prefix() {
    cfg_get ".claude_code.\"$1\".prefix" "$2"
}

if [[ "$(event_enabled "$EVENT")" != "true" ]]; then
    emit_metric "hook" "${EVENT}_disabled" ""
    exit 0
fi

emit_metric "hook" "$EVENT" ""

SUMMARY=""
case "$EVENT" in
    SubagentStop)
        PREFIX="$(event_prefix "$EVENT" '✅')"
        AGENT_TYPE=$(printf '%s' "$PAYLOAD" | jq -r '.agent_type // "agent"' 2>/dev/null)
        LAST_MSG=$(printf '%s' "$PAYLOAD" | jq -r '.last_assistant_message // ""' 2>/dev/null)
        SNIPPET="$(oneline "$LAST_MSG")"
        SUMMARY="${PREFIX} ${AGENT_TYPE} finished"
        [[ -n "$SNIPPET" ]] && SUMMARY="${SUMMARY} — ${SNIPPET}"
        ;;
    Notification)
        PREFIX="$(event_prefix "$EVENT" '🔔')"
        NTYPE=$(printf '%s' "$PAYLOAD" | jq -r '.notification_type // "notice"' 2>/dev/null)
        NMSG=$(printf '%s' "$PAYLOAD" | jq -r '.message // ""' 2>/dev/null)
        SNIPPET="$(oneline "$NMSG")"
        SUMMARY="${PREFIX} ${NTYPE}"
        [[ -n "$SNIPPET" ]] && SUMMARY="${SUMMARY}: ${SNIPPET}"
        ;;
    Stop)
        PREFIX="$(event_prefix "$EVENT" '🏁')"
        LAST_MSG=$(printf '%s' "$PAYLOAD" | jq -r '.last_assistant_message // ""' 2>/dev/null)
        SNIPPET="$(oneline "$LAST_MSG")"
        SUMMARY="${PREFIX} session turn finished"
        [[ -n "$SNIPPET" ]] && SUMMARY="${SUMMARY} — ${SNIPPET}"
        ;;
    SubagentStart)
        PREFIX="$(event_prefix "$EVENT" '🚀')"
        AGENT_TYPE=$(printf '%s' "$PAYLOAD" | jq -r '.agent_type // "agent"' 2>/dev/null)
        SUMMARY="${PREFIX} ${AGENT_TYPE} started"
        ;;
    PreToolUse | PostToolUse)
        PREFIX="$(event_prefix "$EVENT" '🔧')"
        TOOL=$(printf '%s' "$PAYLOAD" | jq -r '.tool_name // "tool"' 2>/dev/null)
        VERB="using"
        [[ "$EVENT" == "PostToolUse" ]] && VERB="used"
        SUMMARY="${PREFIX} ${VERB} ${TOOL}"
        ;;
    PostToolUseFailure)
        PREFIX="$(event_prefix "$EVENT" '⚠️')"
        TOOL=$(printf '%s' "$PAYLOAD" | jq -r '.tool_name // "tool"' 2>/dev/null)
        ERR=$(printf '%s' "$PAYLOAD" | jq -r '.error // ""' 2>/dev/null)
        SNIPPET="$(oneline "$ERR")"
        SUMMARY="${PREFIX} ${TOOL} failed"
        [[ -n "$SNIPPET" ]] && SUMMARY="${SUMMARY}: ${SNIPPET}"
        ;;
    StopFailure)
        PREFIX="$(event_prefix "$EVENT" '🛑')"
        ETYPE=$(printf '%s' "$PAYLOAD" | jq -r '.error_type // "error"' 2>/dev/null)
        SUMMARY="${PREFIX} turn ended in error: ${ETYPE}"
        ;;
    SessionStart)
        PREFIX="$(event_prefix "$EVENT" '🟢')"
        SRC=$(printf '%s' "$PAYLOAD" | jq -r '.source // "startup"' 2>/dev/null)
        SUMMARY="${PREFIX} session started (${SRC})"
        ;;
    SessionEnd)
        PREFIX="$(event_prefix "$EVENT" '🔴')"
        REASON=$(printf '%s' "$PAYLOAD" | jq -r '.end_reason // "unknown"' 2>/dev/null)
        SUMMARY="${PREFIX} session ended (${REASON})"
        ;;
    PreCompact)
        PREFIX="$(event_prefix "$EVENT" '🗜️')"
        TRIGGER=$(printf '%s' "$PAYLOAD" | jq -r '.compaction_trigger // "auto"' 2>/dev/null)
        SUMMARY="${PREFIX} context compacting (${TRIGGER})"
        ;;
    *)
        SUMMARY="ℹ️ Claude Code event: ${EVENT}"
        ;;
esac

[[ -n "$SUMMARY" ]] && "$BRIDGE_DIR/relay-notify.sh" --raw "$SUMMARY" >/dev/null 2>&1

exit 0

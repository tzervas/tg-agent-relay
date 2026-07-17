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
# hooks.Notification (via the hook-notify.sh shim) - see the README's "live
# bridge" note. Use install-hooks.sh (repo root) to wire any of the other 28
# documented events below, driven by relay.toml's [claude_code.<Event>]
# tables. Always exits 0: hook events are advisory (SubagentStop) or
# fire-and-forget (Notification, ...) - this script must never affect
# Claude Code's own control flow, no matter which event fires.
#
# Full documented Claude Code hook event set (verified against
# code.claude.com/docs/en/hooks-guide.md + hooks.md, checked 2026-07-12):
# SessionStart, Setup, UserPromptSubmit, UserPromptExpansion, PreToolUse,
# PermissionRequest, PermissionDenied, PostToolUse, PostToolUseFailure,
# PostToolBatch, Notification, MessageDisplay, SubagentStart, SubagentStop,
# TaskCreated, TaskCompleted, Stop, StopFailure, TeammateIdle,
# InstructionsLoaded, ConfigChange, CwdChanged, FileChanged,
# WorktreeCreate, WorktreeRemove, PreCompact, PostCompact, Elicitation,
# ElicitationResult, SessionEnd. EVERY one of these 30 is handled below
# (see lib/claude-code-events.sh for the canonical list + each event's
# install-time default enabled/disabled + default prefix) - wiring one via
# install-hooks.sh (or a manual settings.json hook entry) needs no code
# change here, only relay.toml configuration.
#
# Field names per event, from the same reference (only the fields this
# adapter actually reads - see the docs for the full per-event schema):
#   SessionStart:       source, model, agent_type, session_title.
#   Setup:               source (init/maintenance).
#   UserPromptSubmit:    prompt.
#   UserPromptExpansion: command (sparsely documented; read defensively).
#   PreToolUse:          tool_name, tool_input.
#   PermissionRequest:   tool_name.
#   PermissionDenied:    tool_name.
#   PostToolUse:         tool_name, tool_input, tool_output.
#   PostToolUseFailure:  tool_name, error_message.
#   PostToolBatch:       (no per-event fields used).
#   Notification:        notification_type, message.
#   MessageDisplay:       content.
#   SubagentStart:       agent_type, agent_id.
#   SubagentStop:        agent_type, last_assistant_message.
#   TaskCreated:         task/description/title (read defensively).
#   TaskCompleted:       task/description/title (read defensively).
#   Stop:                last_assistant_message.
#   StopFailure:         error_type.
#   TeammateIdle:        (no per-event fields used).
#   InstructionsLoaded:  load_reason.
#   ConfigChange:        source, file_path.
#   CwdChanged:          cwd (read defensively).
#   FileChanged:         file_path.
#   WorktreeCreate:      hookSpecificOutput.worktreePath (read defensively).
#   WorktreeRemove:      (no per-event fields used).
#   PreCompact:          trigger.
#   PostCompact:         trigger.
#   Elicitation:         mcp_server_name, action.
#   ElicitationResult:   mcp_server_name, action.
#   SessionEnd:          reason.
# All events also carry hook_event_name (used to dispatch below).
#
# --- Per-event message templates ("format") --------------------------------
# Beyond `enabled`/`prefix`, relay.toml's [claude_code.<Event>] tables accept
# a `format` string with `{placeholder}` interpolation (rendered by
# lib/relay-common.sh's render_template - shared with relay-notify.sh's own
# [generic].format). Every event supports at minimum {prefix} and {event};
# see relay.toml.example's [claude_code.<Event>] blocks for the full
# per-event placeholder list. With NO `format` configured, each event
# renders its ORIGINAL built-in default text below, byte-for-byte -
# backward-compat is by construction, not by convention: the default
# template IS the exact string this adapter has always produced. A
# placeholder referenced in a custom `format` that this event does not
# provide is left LITERAL (never silently blanked) - see render_template's
# own header for why.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/relay-config.sh"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/relay-common.sh"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/claude-code-events.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/routing.sh" ]] && source "$BRIDGE_DIR/lib/routing.sh"

load_relay_config "$BRIDGE_DIR/relay.toml"

PAYLOAD="$(cat 2>/dev/null || true)"
[[ -z "$PAYLOAD" ]] && exit 0

# Claude uses hook_event_name (snake). If we only see Grok's hookEventName
# (or GROK_* env), re-dispatch to the Grok provider — never "Claude Code
# event: unknown" for Grok payloads. (No `exec` after a pipe: it does not
# stop this shell; always exit after re-dispatch.)
_HE_CLAUDE="$(printf '%s' "$PAYLOAD" | jq -r '.hook_event_name // empty' 2>/dev/null)"
_HE_GROK="$(printf '%s' "$PAYLOAD" | jq -r '.hookEventName // empty' 2>/dev/null)"
if [[ -z "$_HE_CLAUDE" || "$_HE_CLAUDE" == "null" ]]; then
    if [[ ( -n "$_HE_GROK" && "$_HE_GROK" != "null" ) || -n "${GROK_HOOK_EVENT:-}" ]]; then
        if [[ -f "$BRIDGE_DIR/adapters/grok.sh" ]]; then
            printf '%s' "$PAYLOAD" | bash "$BRIDGE_DIR/adapters/grok.sh"
            exit 0
        fi
    fi
fi
EVENT="${_HE_CLAUDE:-unknown}"
[[ -z "$EVENT" || "$EVENT" == "null" ]] && EVENT="unknown"

# Prefer providers/claude via provider_hook when Python is available (issue #59).
# Force shell case: CLAUDE_USE_PROVIDER_HOOK=0 (or PROVIDER_HOOK=0).
# Force Python:    CLAUDE_USE_PROVIDER_HOOK=1
_CC_USE_PY="${CLAUDE_USE_PROVIDER_HOOK:-${PROVIDER_HOOK:-}}"
if [[ -z "$_CC_USE_PY" ]]; then
    if [[ -f "$BRIDGE_DIR/lib/provider_hook.py" ]]; then
        # shellcheck disable=SC1091
        [[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
        declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
        if relay_python -c "import providers.claude" >/dev/null 2>&1; then
            _CC_USE_PY=1
        else
            _CC_USE_PY=0
        fi
    else
        _CC_USE_PY=0
    fi
fi
if [[ "$_CC_USE_PY" == "1" ]]; then
    # shellcheck disable=SC1091
    [[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
    declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
    if [[ -f "$BRIDGE_DIR/lib/provider_hook.py" ]]; then
        _CC_CFG_JSON="$(mktemp)"
        if command -v jq >/dev/null 2>&1 && [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
            printf '%s' "$RELAY_CONFIG_JSON" | jq -c '{claude_code: (.claude_code // {})}' \
                >"$_CC_CFG_JSON" 2>/dev/null || printf '%s\n' '{}' >"$_CC_CFG_JSON"
        else
            printf '%s\n' '{}' >"$_CC_CFG_JSON"
        fi
        _CC_OUT="$(printf '%s' "$PAYLOAD" | relay_python "$BRIDGE_DIR/lib/provider_hook.py" claude \
            --config-json "$_CC_CFG_JSON" 2>/dev/null)" || _CC_OUT=""
        rm -f "$_CC_CFG_JSON"
        _CC_LINE1="$(printf '%s\n' "$_CC_OUT" | head -1)"
        case "$_CC_LINE1" in
            SKIP:*)
                emit_metric "hook" "${EVENT}_skip" "${_CC_LINE1#SKIP:}"
                exit 0
                ;;
            OK:*)
                SUMMARY="${_CC_LINE1#OK:}"
                emit_metric "hook" "$EVENT" "provider"
                export RELAY_BACKEND="${RELAY_BACKEND:-claude}"
                if [[ -z "${RELAY_PROJECT:-}" ]] && declare -f project_from_cwd >/dev/null 2>&1; then
                    _cc_cwd="$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)"
                    [[ -z "$_cc_cwd" || "$_cc_cwd" == "null" ]] && _cc_cwd="${CLAUDE_PROJECT_DIR:-$(pwd)}"
                    RELAY_PROJECT="$(project_from_cwd "$_cc_cwd")"
                    [[ -n "$RELAY_PROJECT" ]] && export RELAY_PROJECT
                fi
                if [[ -n "$SUMMARY" ]]; then
                    export RELAY_HOOK_EVENT="$EVENT"
                    _cc_cwd2="$(printf '%s' "$PAYLOAD" | jq -r '.cwd // empty' 2>/dev/null)"
                    [[ -z "$_cc_cwd2" || "$_cc_cwd2" == "null" ]] && _cc_cwd2="${CLAUDE_PROJECT_DIR:-}"
                    [[ -n "$_cc_cwd2" && -d "$_cc_cwd2" ]] && export RELAY_CWD="$_cc_cwd2"
                    TG_SEND_SOURCE=hook "$BRIDGE_DIR/relay-notify.sh" --raw "$SUMMARY" >/dev/null 2>&1
                fi
                exit 0
                ;;
        esac
        # Fall through to shell formatting if provider path failed.
    fi
fi

# pf <jq-filter> - read one field off $PAYLOAD, "" on any parse failure.
pf() {
    printf '%s' "$PAYLOAD" | jq -r "$1" 2>/dev/null
}

# event_enabled <EventName> -> "true"/"false"
# relay.toml [claude_code.<EventName>].enabled, falling back to that
# event's install-time default (lib/claude-code-events.sh) rather than a
# single hardcoded default - see that file's header for the backward-compat
# argument (the two events already live, SubagentStop/Notification, keep
# defaulting to enabled either way).
event_enabled() {
    cfg_get ".claude_code.\"$1\".enabled" "$(cc_event_default_enabled "$1")"
}

# event_prefix <EventName> <built-in-default-prefix>
event_prefix() {
    cfg_get ".claude_code.\"$1\".prefix" "$2"
}

# event_format <EventName> -> relay.toml [claude_code.<EventName>].format,
# or "" (meaning: use this event's built-in default template).
event_format() {
    cfg_get ".claude_code.\"$1\".format" ""
}

if [[ "$(event_enabled "$EVENT")" != "true" ]]; then
    emit_metric "hook" "${EVENT}_disabled" ""
    exit 0
fi

emit_metric "hook" "$EVENT" ""

SUMMARY=""
case "$EVENT" in
    SessionStart)
        PREFIX="$(event_prefix "$EVENT" '🟢')"
        SRC=$(pf '.source // "startup"')
        MODEL=$(pf '.model // ""')
        TITLE=$(pf '.session_title // ""')
        AGENT_TYPE=$(pf '.agent_type // ""')
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} session started ({source\})}" \
            prefix "$PREFIX" event "$EVENT" source "$SRC" model "$MODEL" \
            session_title "$TITLE" agent "$AGENT_TYPE")"
        ;;
    Setup)
        PREFIX="$(event_prefix "$EVENT" '⚙️')"
        SRC=$(pf '.source // "init"')
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} setup ({source\})}" prefix "$PREFIX" event "$EVENT" source "$SRC")"
        ;;
    UserPromptSubmit)
        PREFIX="$(event_prefix "$EVENT" '⌨️')"
        SNIPPET="$(oneline "$(pf '.prompt // ""')")"
        DETAIL_SUFFIX=""
        [[ -n "$SNIPPET" ]] && DETAIL_SUFFIX=": ${SNIPPET}"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} prompt submitted{detail_suffix\}}" \
            prefix "$PREFIX" event "$EVENT" prompt "$SNIPPET" message "$SNIPPET" detail_suffix "$DETAIL_SUFFIX")"
        ;;
    UserPromptExpansion)
        PREFIX="$(event_prefix "$EVENT" '🧩')"
        SNIPPET="$(oneline "$(pf '.command // .prompt // ""')")"
        DETAIL_SUFFIX=""
        [[ -n "$SNIPPET" ]] && DETAIL_SUFFIX=": ${SNIPPET}"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} prompt expansion{detail_suffix\}}" \
            prefix "$PREFIX" event "$EVENT" message "$SNIPPET" detail_suffix "$DETAIL_SUFFIX")"
        ;;
    PreToolUse | PostToolUse)
        PREFIX="$(event_prefix "$EVENT" '🔧')"
        TOOL=$(pf '.tool_name // "tool"')
        VERB="using"
        [[ "$EVENT" == "PostToolUse" ]] && VERB="used"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} {verb\} {tool\}}" \
            prefix "$PREFIX" event "$EVENT" tool "$TOOL" verb "$VERB")"
        ;;
    PostToolUseFailure)
        PREFIX="$(event_prefix "$EVENT" '⚠️')"
        TOOL=$(pf '.tool_name // "tool"')
        SNIPPET="$(oneline "$(pf '.error_message // .error // ""')")"
        DETAIL_SUFFIX=""
        [[ -n "$SNIPPET" ]] && DETAIL_SUFFIX=": ${SNIPPET}"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} {tool\} failed{detail_suffix\}}" \
            prefix "$PREFIX" event "$EVENT" tool "$TOOL" message "$SNIPPET" detail_suffix "$DETAIL_SUFFIX")"
        ;;
    PostToolBatch)
        PREFIX="$(event_prefix "$EVENT" '📦')"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} tool batch completed}" prefix "$PREFIX" event "$EVENT")"
        ;;
    PermissionRequest)
        PREFIX="$(event_prefix "$EVENT" '🔐')"
        TOOL=$(pf '.tool_name // "tool"')
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} permission requested for {tool\}}" \
            prefix "$PREFIX" event "$EVENT" tool "$TOOL")"
        ;;
    PermissionDenied)
        PREFIX="$(event_prefix "$EVENT" '🚫')"
        TOOL=$(pf '.tool_name // "tool"')
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} {tool\} denied}" prefix "$PREFIX" event "$EVENT" tool "$TOOL")"
        ;;
    Stop)
        PREFIX="$(event_prefix "$EVENT" '🏁')"
        SNIPPET="$(oneline "$(pf '.last_assistant_message // ""')")"
        DETAIL_SUFFIX=""
        [[ -n "$SNIPPET" ]] && DETAIL_SUFFIX=" — ${SNIPPET}"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} session turn finished{detail_suffix\}}" \
            prefix "$PREFIX" event "$EVENT" message "$SNIPPET" detail_suffix "$DETAIL_SUFFIX")"
        ;;
    StopFailure)
        PREFIX="$(event_prefix "$EVENT" '🛑')"
        ETYPE=$(pf '.error_type // "error"')
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} turn ended in error: {error_type\}}" \
            prefix "$PREFIX" event "$EVENT" error_type "$ETYPE")"
        ;;
    SubagentStart)
        PREFIX="$(event_prefix "$EVENT" '🚀')"
        AGENT_TYPE=$(pf '.agent_type // "agent"')
        AGENT_ID=$(pf '.agent_id // ""')
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} {agent\} started}" \
            prefix "$PREFIX" event "$EVENT" agent "$AGENT_TYPE" agent_id "$AGENT_ID")"
        ;;
    SubagentStop)
        PREFIX="$(event_prefix "$EVENT" '✅')"
        AGENT_TYPE=$(pf '.agent_type // "agent"')
        SNIPPET="$(oneline "$(pf '.last_assistant_message // ""')")"
        DETAIL_SUFFIX=""
        [[ -n "$SNIPPET" ]] && DETAIL_SUFFIX=" — ${SNIPPET}"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} {agent\} finished{detail_suffix\}}" \
            prefix "$PREFIX" event "$EVENT" agent "$AGENT_TYPE" message "$SNIPPET" detail_suffix "$DETAIL_SUFFIX")"
        ;;
    TeammateIdle)
        PREFIX="$(event_prefix "$EVENT" '💤')"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} teammate idle}" prefix "$PREFIX" event "$EVENT")"
        ;;
    TaskCreated | TaskCompleted)
        PREFIX="$(event_prefix "$EVENT" "$(cc_event_default_prefix "$EVENT")")"
        SNIPPET="$(oneline "$(pf '.task // .description // .title // ""')")"
        DETAIL_SUFFIX=""
        [[ -n "$SNIPPET" ]] && DETAIL_SUFFIX=": ${SNIPPET}"
        VERB="created"
        [[ "$EVENT" == "TaskCompleted" ]] && VERB="completed"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} task {verb\}{detail_suffix\}}" \
            prefix "$PREFIX" event "$EVENT" verb "$VERB" message "$SNIPPET" detail_suffix "$DETAIL_SUFFIX")"
        ;;
    ConfigChange)
        PREFIX="$(event_prefix "$EVENT" '⚙️')"
        SRC=$(pf '.source // "settings"')
        FILE=$(pf '.file_path // ""')
        DETAIL_SUFFIX=""
        [[ -n "$FILE" ]] && DETAIL_SUFFIX=": ${FILE}"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} config changed ({source\}){detail_suffix\}}" \
            prefix "$PREFIX" event "$EVENT" source "$SRC" file "$FILE" detail_suffix "$DETAIL_SUFFIX")"
        ;;
    CwdChanged)
        PREFIX="$(event_prefix "$EVENT" '📂')"
        CWD=$(pf '.cwd // .new_cwd // ""')
        DETAIL_SUFFIX=""
        [[ -n "$CWD" ]] && DETAIL_SUFFIX=": ${CWD}"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} working directory changed{detail_suffix\}}" \
            prefix "$PREFIX" event "$EVENT" cwd "$CWD" detail_suffix "$DETAIL_SUFFIX")"
        ;;
    FileChanged)
        PREFIX="$(event_prefix "$EVENT" '📝')"
        FILE=$(pf '.file_path // ""')
        DETAIL_SUFFIX=""
        [[ -n "$FILE" ]] && DETAIL_SUFFIX=": ${FILE}"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} file changed{detail_suffix\}}" \
            prefix "$PREFIX" event "$EVENT" file "$FILE" detail_suffix "$DETAIL_SUFFIX")"
        ;;
    InstructionsLoaded)
        PREFIX="$(event_prefix "$EVENT" '📖')"
        REASON=$(pf '.load_reason // "session_start"')
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} instructions loaded ({reason\})}" \
            prefix "$PREFIX" event "$EVENT" reason "$REASON")"
        ;;
    PreCompact)
        PREFIX="$(event_prefix "$EVENT" '🗜️')"
        TRIGGER=$(pf '.trigger // .compaction_trigger // "auto"')
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} context compacting ({trigger\})}" \
            prefix "$PREFIX" event "$EVENT" trigger "$TRIGGER")"
        ;;
    PostCompact)
        PREFIX="$(event_prefix "$EVENT" '📦')"
        TRIGGER=$(pf '.trigger // "auto"')
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} context compaction finished ({trigger\})}" \
            prefix "$PREFIX" event "$EVENT" trigger "$TRIGGER")"
        ;;
    WorktreeCreate)
        PREFIX="$(event_prefix "$EVENT" '🌳')"
        WPATH=$(pf '.hookSpecificOutput.worktreePath // .worktreePath // ""')
        DETAIL_SUFFIX=""
        [[ -n "$WPATH" ]] && DETAIL_SUFFIX=": ${WPATH}"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} worktree created{detail_suffix\}}" \
            prefix "$PREFIX" event "$EVENT" path "$WPATH" detail_suffix "$DETAIL_SUFFIX")"
        ;;
    WorktreeRemove)
        PREFIX="$(event_prefix "$EVENT" '🪓')"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} worktree removed}" prefix "$PREFIX" event "$EVENT")"
        ;;
    Elicitation | ElicitationResult)
        PREFIX="$(event_prefix "$EVENT" "$(cc_event_default_prefix "$EVENT")")"
        SERVER=$(pf '.mcp_server_name // "mcp-server"')
        ACTION=$(pf '.action // ""')
        FORMAT="$(event_format "$EVENT")"
        if [[ "$EVENT" == "Elicitation" ]]; then
            SUMMARY="$(render_template "${FORMAT:-{prefix\} MCP elicitation requested ({server\})}" \
                prefix "$PREFIX" event "$EVENT" server "$SERVER" action "$ACTION")"
        else
            SUMMARY="$(render_template "${FORMAT:-{prefix\} MCP elicitation resolved ({server\}: {action\})}" \
                prefix "$PREFIX" event "$EVENT" server "$SERVER" action "$ACTION")"
        fi
        ;;
    Notification)
        PREFIX="$(event_prefix "$EVENT" '🔔')"
        NTYPE=$(pf '.notification_type // "notice"')
        SNIPPET="$(oneline "$(pf '.message // ""')")"
        DETAIL_SUFFIX=""
        [[ -n "$SNIPPET" ]] && DETAIL_SUFFIX=": ${SNIPPET}"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} {notification_type\}{detail_suffix\}}" \
            prefix "$PREFIX" event "$EVENT" notification_type "$NTYPE" message "$SNIPPET" detail_suffix "$DETAIL_SUFFIX")"
        ;;
    MessageDisplay)
        PREFIX="$(event_prefix "$EVENT" '💬')"
        SNIPPET="$(oneline "$(pf '.content // .message // ""')")"
        DETAIL_SUFFIX=""
        [[ -n "$SNIPPET" ]] && DETAIL_SUFFIX=": ${SNIPPET}"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} message displayed{detail_suffix\}}" \
            prefix "$PREFIX" event "$EVENT" message "$SNIPPET" detail_suffix "$DETAIL_SUFFIX")"
        ;;
    SessionEnd)
        PREFIX="$(event_prefix "$EVENT" '🔴')"
        REASON=$(pf '.reason // .end_reason // "unknown"')
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} session ended ({reason\})}" \
            prefix "$PREFIX" event "$EVENT" reason "$REASON")"
        ;;
    *)
        PREFIX="$(event_prefix "$EVENT" 'ℹ️')"
        FORMAT="$(event_format "$EVENT")"
        SUMMARY="$(render_template "${FORMAT:-{prefix\} Claude Code event: {event\}}" prefix "$PREFIX" event "$EVENT")"
        ;;
esac

# TG_SEND_SOURCE=hook tags this as an automated hook ping - it's a real
# environment variable (not a shell-local one), so it's inherited
# automatically all the way through relay-notify.sh's own call to
# tg-send.sh with no extra plumbing on either script's part. tg-send.sh
# uses it to give hook pings a voice read-through even when long/
# paginated (see its header's "Hook audio" section, relay.toml [tts]).
# RELAY_BACKEND / RELAY_PROJECT for multi-chat reverse-lookup (harmless when
# routing is unset — relay-notify only tags when routing is configured).
export RELAY_BACKEND="${RELAY_BACKEND:-claude}"
if [[ -z "${RELAY_PROJECT:-}" ]] && declare -f project_from_cwd >/dev/null 2>&1; then
    _cc_cwd="$(pf '.cwd // empty')"
    [[ -z "$_cc_cwd" || "$_cc_cwd" == "null" ]] && _cc_cwd="${CLAUDE_PROJECT_DIR:-$(pwd)}"
    RELAY_PROJECT="$(project_from_cwd "$_cc_cwd")"
    [[ -n "$RELAY_PROJECT" ]] && export RELAY_PROJECT
fi
if [[ -n "$SUMMARY" ]]; then
    # shellcheck disable=SC1091
    [[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
    declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
    _CC_TOOL="$(pf '.tool_name // "tool"')"
    if command -v "${RELAY_PYTHON:-python3}" >/dev/null 2>&1; then
        _CC_FILTERED="$(printf '%s' "$SUMMARY" | relay_python -c "
from tg_agent_relay.goal_events import filter_hook_summary
import sys
s=sys.stdin.read()
r=filter_hook_summary(s, tool_name='${_CC_TOOL}', hook_event='${EVENT}')
print('' if r is None else r)
" 2>/dev/null)" || _CC_FILTERED=""
        [[ -n "$_CC_FILTERED" ]] && SUMMARY="$_CC_FILTERED"
    fi
    if [[ -n "$SUMMARY" ]]; then
        export RELAY_HOOK_EVENT="$EVENT"
        _cc_cwd="$(pf '.cwd // empty')"
        [[ -z "$_cc_cwd" || "$_cc_cwd" == "null" ]] && _cc_cwd="${CLAUDE_PROJECT_DIR:-$(pwd)}"
        [[ -n "$_cc_cwd" && -d "$_cc_cwd" ]] && export RELAY_CWD="$_cc_cwd"
        TG_SEND_SOURCE=hook "$BRIDGE_DIR/relay-notify.sh" --raw "$SUMMARY" >/dev/null 2>&1
    fi
fi

exit 0

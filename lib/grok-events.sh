#!/bin/bash
# lib/grok-events.sh - Shell mirror of the Grok Build hook catalog.
#
# CANONICAL source of truth is now providers/grok/hooks.py (and
# lib/provider_catalog.py / install-grok-hooks.sh). This file remains for
# shell fallbacks and older tests. Keep GROK_EVENTS in sync with the
# Python catalog (14 events).
#
# Documented Grok hook events (user-guide/10-hooks.md): SessionStart,
# UserPromptSubmit, PreToolUse, PostToolUse, PostToolUseFailure,
# PermissionDenied, Stop, StopFailure, Notification, SubagentStart,
# SubagentStop (SubagentEnd alias), PreCompact, PostCompact, SessionEnd.
set -u

# shellcheck disable=SC2034  # consumed by sourcing scripts
GROK_EVENTS=(
    SessionStart
    UserPromptSubmit
    PreToolUse PostToolUse PostToolUseFailure
    PermissionDenied
    Stop StopFailure
    Notification
    SubagentStart SubagentStop
    PreCompact PostCompact
    SessionEnd
)

# grok_event_default_enabled <Event> -> "true"/"false"
#
# Install-time + adapter fallback defaults. Low-volume lifecycle/error
# signals default on; high-volume tool/prompt events are opt-in.
# Unrecognized/future event names default on (same contract as Claude:
# if someone wired it, notify unless told not to).
grok_event_default_enabled() {
    case "$1" in
        Stop | StopFailure | SubagentStop | Notification | PostToolUseFailure)
            printf 'true' ;;
        SessionStart | UserPromptSubmit | PreToolUse | PostToolUse | \
            PermissionDenied | SubagentStart | PreCompact | PostCompact | \
            SessionEnd)
            printf 'false' ;;
        *)
            printf 'true' ;;
    esac
}

# grok_event_default_prefix <Event> -> emoji default.
grok_event_default_prefix() {
    case "$1" in
        SessionStart) printf '🟢' ;;
        UserPromptSubmit) printf '⌨️' ;;
        PreToolUse | PostToolUse) printf '🔧' ;;
        PostToolUseFailure) printf '⚠️' ;;
        PermissionDenied) printf '🚫' ;;
        Stop) printf '🏁' ;;
        StopFailure) printf '🛑' ;;
        Notification) printf '🔔' ;;
        SubagentStart) printf '🚀' ;;
        SubagentStop) printf '✅' ;;
        PreCompact) printf '🗜️' ;;
        PostCompact) printf '📦' ;;
        SessionEnd) printf '🔴' ;;
        *) printf 'ℹ️' ;;
    esac
}

# grok_normalize_event <raw> -> PascalCase EventName
#
# Grok payloads use snake values ("pre_tool_use") and/or camel Cursor
# aliases ("preToolUse"); Claude-compat may pass PascalCase. Always emit
# the canonical PascalCase name used in GROK_EVENTS / relay.toml tables.
grok_normalize_event() {
    local raw="${1:-}"
    # Already PascalCase documented name?
    case "$raw" in
        SessionStart|UserPromptSubmit|PreToolUse|PostToolUse|PostToolUseFailure|\
        PermissionDenied|Stop|StopFailure|Notification|SubagentStart|SubagentStop|\
        PreCompact|PostCompact|SessionEnd)
            printf '%s' "$raw"
            return 0
            ;;
        SubagentEnd)
            printf 'SubagentStop'
            return 0
            ;;
    esac

    # snake_case / lowercase
    local lower
    lower="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]')"
    case "$lower" in
        session_start|sessionstart) printf 'SessionStart' ;;
        user_prompt_submit|userpromptsubmit|beforesubmitprompt) printf 'UserPromptSubmit' ;;
        pre_tool_use|pretooluse|beforeshellexecution|beforemcpexecution|beforereadfile) printf 'PreToolUse' ;;
        post_tool_use|posttooluse|aftershellexecution|aftermcpexecution|afterfileedit|\
        afteragentresponse|afteragentthought) printf 'PostToolUse' ;;
        post_tool_use_failure|posttoolusefailure) printf 'PostToolUseFailure' ;;
        permission_denied|permissiondenied) printf 'PermissionDenied' ;;
        stop) printf 'Stop' ;;
        stop_failure|stopfailure) printf 'StopFailure' ;;
        notification) printf 'Notification' ;;
        subagent_start|subagentstart) printf 'SubagentStart' ;;
        subagent_stop|subagentstop|subagent_end|subagentend) printf 'SubagentStop' ;;
        pre_compact|precompact) printf 'PreCompact' ;;
        post_compact|postcompact) printf 'PostCompact' ;;
        session_end|sessionend) printf 'SessionEnd' ;;
        *)
            # Best-effort: snake_case -> PascalCase words
            if [[ "$lower" == *_* ]]; then
                local part out=""
                IFS='_' read -ra parts <<< "$lower"
                for part in "${parts[@]}"; do
                    [[ -z "$part" ]] && continue
                    out+="$(printf '%s%s' "$(printf '%s' "${part:0:1}" | tr '[:lower:]' '[:upper:]')" "${part:1}")"
                done
                printf '%s' "$out"
            else
                printf '%s' "$raw"
            fi
            ;;
    esac
}

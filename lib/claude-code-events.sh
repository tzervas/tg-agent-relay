#!/bin/bash
# lib/claude-code-events.sh - Shell catalog for the documented Claude Code
# hook event set: names + install-time default enabled/disabled + default
# prefix. Sourced by adapters/claude-code.sh (runtime per-event
# enable/prefix/format gate). install-hooks.sh prefers providers/claude via
# lib/provider_catalog.py and uses this file only as a no-Python fallback so
# shell and catalog defaults stay aligned. Sourced, never executed directly.
#
# The full documented Claude Code hook event set (verified against
# code.claude.com/docs/en/hooks-guide.md + hooks.md, checked 2026-07-12):
# SessionStart, Setup, UserPromptSubmit, UserPromptExpansion, PreToolUse,
# PermissionRequest, PermissionDenied, PostToolUse, PostToolUseFailure,
# PostToolBatch, Notification, MessageDisplay, SubagentStart, SubagentStop,
# TaskCreated, TaskCompleted, Stop, StopFailure, TeammateIdle,
# InstructionsLoaded, ConfigChange, CwdChanged, FileChanged,
# WorktreeCreate, WorktreeRemove, PreCompact, PostCompact, Elicitation,
# ElicitationResult, SessionEnd. All 30 are handled in
# adapters/claude-code.sh's dispatch (see that file's header for the
# per-event field reference).
set -u

# shellcheck disable=SC2034  # consumed by sourcing scripts
CLAUDE_CODE_EVENTS=(
    SessionStart Setup
    UserPromptSubmit UserPromptExpansion
    PreToolUse PostToolUse PostToolUseFailure PostToolBatch
    PermissionRequest PermissionDenied
    Stop StopFailure
    SubagentStart SubagentStop TeammateIdle
    TaskCreated TaskCompleted
    ConfigChange CwdChanged FileChanged InstructionsLoaded
    PreCompact PostCompact
    WorktreeCreate WorktreeRemove
    Elicitation ElicitationResult
    Notification MessageDisplay
    SessionEnd
)

# cc_event_default_enabled <Event> -> "true"/"false"
#
# The INSTALL-TIME default (what install-hooks.sh wires when relay.toml
# does not say otherwise) AND the ADAPTER's own per-event fallback (what
# fires when a hook IS wired into settings.json but relay.toml has no
# [claude_code.<Event>] "enabled" override). Five low-volume, high-signal
# lifecycle events default on; the other 25 DOCUMENTED events are opt-in
# (high-volume, or rarely worth a phone ping) - this makes
# relay.toml.example's long-standing per-event commented guidance
# ("enabled = false # noisy...") the actual enforced default instead of a
# comment with no runtime effect. Any event OUTSIDE the documented 30 (a
# genuinely unrecognized/future hook_event_name) still defaults to
# enabled=true - the ORIGINAL universal default, preserved deliberately:
# such an event only ever fires at all if someone has already gone out of
# their way to wire it into settings.json, so "notify, unless told not to"
# stays the right default for the unknown case (see
# adapters/claude-code.sh's `*` catch-all branch, and
# tests/run-tests.sh's "unknown event" backward-compat case).
#
# Backward-compat note: the only two events already wired into a live
# ~/.claude/settings.json before this table existed - SubagentStop and
# Notification - are both in the default-true set below, so an existing
# live bridge's behavior is unchanged by this table's introduction.
cc_event_default_enabled() {
    case "$1" in
        SubagentStop | Notification | Stop | PostToolUseFailure | StopFailure)
            printf 'true' ;;
        SessionStart | Setup | UserPromptSubmit | UserPromptExpansion | \
            PreToolUse | PostToolUse | PostToolBatch | \
            PermissionRequest | PermissionDenied | \
            SubagentStart | TeammateIdle | \
            TaskCreated | TaskCompleted | \
            ConfigChange | CwdChanged | FileChanged | InstructionsLoaded | \
            PreCompact | PostCompact | \
            WorktreeCreate | WorktreeRemove | \
            Elicitation | ElicitationResult | \
            MessageDisplay | SessionEnd)
            printf 'false' ;;
        *)
            # Not one of the 30 documented events - unrecognized/future.
            printf 'true' ;;
    esac
}

# cc_event_default_prefix <Event> -> emoji default.
#
# Mirrors the per-event defaults baked into adapters/claude-code.sh's case
# statement (kept here too, in one place, so install-hooks.sh can print a
# human-readable plan without sourcing the whole adapter - and so a new
# event only ever needs its default added here + its dispatch branch added
# to the adapter, never both places re-deriving the same emoji by hand).
cc_event_default_prefix() {
    case "$1" in
        SessionStart) printf '🟢' ;;
        Setup) printf '⚙️' ;;
        UserPromptSubmit) printf '⌨️' ;;
        UserPromptExpansion) printf '🧩' ;;
        PreToolUse | PostToolUse) printf '🔧' ;;
        PostToolUseFailure) printf '⚠️' ;;
        PostToolBatch) printf '📦' ;;
        PermissionRequest) printf '🔐' ;;
        PermissionDenied) printf '🚫' ;;
        Stop) printf '🏁' ;;
        StopFailure) printf '🛑' ;;
        SubagentStart) printf '🚀' ;;
        SubagentStop) printf '✅' ;;
        TeammateIdle) printf '💤' ;;
        TaskCreated) printf '📋' ;;
        TaskCompleted) printf '☑️' ;;
        ConfigChange) printf '⚙️' ;;
        CwdChanged) printf '📂' ;;
        FileChanged) printf '📝' ;;
        InstructionsLoaded) printf '📖' ;;
        PreCompact) printf '🗜️' ;;
        PostCompact) printf '📦' ;;
        WorktreeCreate) printf '🌳' ;;
        WorktreeRemove) printf '🪓' ;;
        Elicitation) printf '❓' ;;
        ElicitationResult) printf '✔️' ;;
        Notification) printf '🔔' ;;
        MessageDisplay) printf '💬' ;;
        SessionEnd) printf '🔴' ;;
        *) printf 'ℹ️' ;;
    esac
}

#!/bin/bash
# tg-poll.sh - Inbound long-poll: Telegram phone -> stdout event stream.
#
# Meant to be run as the main Claude Code session's `Monitor` event source
# (stdout = the event stream). Prints exactly ONE line per allowed user
# message, in the form:
#   [telegram] <text>
#
# Emits ONLY real allowed-user messages - no heartbeats, no noise - so the
# session pays zero tokens until the user actually sends something.
#
# SECURITY BOUNDARY: strict allowlist by numeric ALLOWED_USER_ID. Every other
# sender is silently ignored (never printed, never forwarded).
#
# Setup discovery: if ALLOWED_USER_ID is empty, an incoming message's sender
# id is printed as "[telegram-setup] your user_id is <id>" instead of being
# forwarded, so the user can discover + set it (go-live.sh also automates this).
#
# Robustness: never exits on a bad/failed poll; every curl call is defensive
# (`|| true` / timeout + retry-sleep), offset is persisted per-update so a
# restart never re-delivers or drops a message.
#
# Inbound reassembly: consecutive messages from the allowed user are buffered
# to a durable file and combined into a SINGLE "[telegram] ..." event once
# TG_REASSEMBLE_WINDOW seconds (default 4) pass with no new message - so a
# message your Telegram client split into several parts, or a burst of rapid
# follow-ups, forwards to Claude Code as ONE event instead of several. This
# matters mechanically, not just cosmetically: the Monitor event stream
# treats each stdout LINE as one notification, so the combined emission must
# itself be a single physical line - messages are joined in arrival order
# with " ⏎ " between them (any newlines within an individual message are
# flattened to spaces so a multi-line Telegram message can't reintroduce
# extra stdout lines). Buffered messages are stored delimited by ASCII RS
# (0x1e, never appears in normal text) rather than newline, so one message's
# internal newlines can never be confused with a message boundary. Every
# update still advances the offset (never redelivered); a message is
# appended to the durable buffer file BEFORE its offset is advanced, so a
# crash between receipt and offset-advance can't lose it (worst case:
# Telegram redelivers and it is re-buffered once, harmlessly). A transient
# getUpdates failure never touches or drops the buffer - it's only
# read/written on a successful poll.
#
# In-chat commands (user -> agent): if relay.toml defines a [commands.<name>]
# table, a flushed message starting with its `slash` ("/status") or
# `keyword` ("status") form is tagged "[telegram:cmd:<tag>] <text>" instead
# of the plain "[telegram] <text>" - so the consuming agent/hook can
# recognize and act on it. BACKWARD-COMPAT: with no relay.toml (or no
# [commands.*] section), NO message is ever tagged - every flush is
# byte-for-byte the plain "[telegram] <text>" this script has always
# emitted. See classify_command() below.
#
# This file is SOURCEABLE for tests: the poll loop itself only runs when the
# script is executed directly (see the `[[ "${BASH_SOURCE[0]}" == "$0" ]]`
# guard at the bottom), so `source tg-poll.sh` (as tests/ does) loads
# flush_buffer/classify_command/etc. without blocking on the infinite loop.
# Running it normally (`bash tg-poll.sh` / the Monitor invocation) is
# unaffected - main() runs the exact same loop that used to be inlined here.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$BRIDGE_DIR/.env"
OFFSET_FILE="$BRIDGE_DIR/.offset"
BUFFER_FILE="$BRIDGE_DIR/.tg-buffer"
BUFFER_TS_FILE="$BRIDGE_DIR/.tg-buffer-ts"

# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"
if declare -f load_relay_config >/dev/null 2>&1; then
    load_relay_config "$BRIDGE_DIR/relay.toml"
else
    cfg_get() { printf '%s' "$2"; }        # lib missing (shouldn't happen) -> default-only shim
    cfg_has_section() { return 1; }
fi
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-common.sh" ]] && source "$BRIDGE_DIR/lib/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }  # lib missing -> no-op shim

# Quiet window (seconds) with no new message before a buffered burst flushes
# as one combined event.
REASSEMBLE_WINDOW="${TG_REASSEMBLE_WINDOW:-$(cfg_get '.general.reassemble_window' 4)}"
[[ "$REASSEMBLE_WINDOW" =~ ^[0-9]+$ ]] || REASSEMBLE_WINDOW=4

# classify_command <flattened-text>
#
# Prints the matched command's TABLE NAME (e.g. "status", the relay.toml
# [commands.<name>] key - NOT necessarily its display tag, see
# command_field below) if $1 starts with that command's `slash` or
# `keyword` form, else prints nothing (and always returns 0 - "no command
# matched" is not an error). Reads the relay.toml [commands.*] tables
# loaded into $RELAY_CONFIG_JSON. Never matches anything when no
# [commands.*] section is configured - the backward-compat guarantee
# described in the header note above.
classify_command() {
    local text="$1"
    cfg_has_section "commands" || return 0

    local entries entry name slash keyword
    entries="$(printf '%s' "$RELAY_CONFIG_JSON" | jq -c '.commands // {} | to_entries[]?' 2>/dev/null)"
    [[ -z "$entries" ]] && return 0

    while IFS= read -r entry; do
        [[ -z "$entry" ]] && continue
        name="$(printf '%s' "$entry" | jq -r '.key')"
        slash="$(printf '%s' "$entry" | jq -r '.value.slash // empty')"
        keyword="$(printf '%s' "$entry" | jq -r '.value.keyword // empty')"

        if [[ -n "$slash" ]] && { [[ "$text" == "$slash" ]] || [[ "$text" == "${slash} "* ]]; }; then
            printf '%s' "$name"
            return 0
        fi
        if [[ -n "$keyword" ]] && { [[ "$text" == "$keyword" ]] || [[ "$text" == "${keyword} "* ]] || [[ "$text" == "${keyword}:"* ]]; }; then
            printf '%s' "$name"
            return 0
        fi
    done <<< "$entries"

    return 0
}

# command_field <name> <field> <default>
#
# One relay.toml [commands.<name>].<field> value, or <default> if absent.
command_field() {
    local name="$1" field="$2" default="$3"
    cfg_get ".commands.\"$name\".\"$field\"" "$default"
}

# dispatch_command <name> <flattened-text>
#
# Routing SEAM for relay-handled commands (a follow-up feature - not built
# here; this is the extension point for it, see ROADMAP.md "Next"). A
# command's relay.toml `mode` is:
#   "forward" (default, the ONLY behavior actually shipped today) - print
#     "[telegram:cmd:<tag>] <text>" for the consuming agent, same as
#     before this seam existed.
#   "relay" - the relay handles it ITSELF via `handler` (a script path,
#     relative to $BRIDGE_DIR or absolute) and prints NOTHING to stdout -
#     zero model tokens, since the agent/Monitor never even sees the
#     event. The handler receives the flattened text as $1 and is
#     responsible for its own reply (e.g. calling relay-notify.sh/
#     tg-send.sh itself). Launched detached (backgrounded, output
#     discarded) so a slow/hanging handler can never block the poll loop.
# A "relay" mode with no configured (or non-executable) `handler` is a
# no-op, never a crash - see the handlers/ directory for the handler
# contract once one is actually built.
dispatch_command() {
    local name="$1" text="$2"
    local mode tag handler

    mode="$(command_field "$name" mode forward)"

    if [[ "$mode" == "relay" ]]; then
        emit_metric "tg-poll" "command_relay_handled" "$name"
        handler="$(command_field "$name" handler '')"
        if [[ -n "$handler" ]]; then
            if [[ "$handler" == /* && -x "$handler" ]]; then
                "$handler" "$text" >/dev/null 2>&1 &
            elif [[ -x "$BRIDGE_DIR/$handler" ]]; then
                "$BRIDGE_DIR/$handler" "$text" >/dev/null 2>&1 &
            fi
        fi
        return 0  # relay-handled: nothing emitted to the agent/Monitor stream.
    fi

    tag="$(command_field "$name" tag "$name")"
    emit_metric "tg-poll" "command_forwarded" "$name"
    printf '[telegram:cmd:%s] %s\n' "$tag" "$text"
}

# Emit the buffered burst (if any) as one combined "[telegram] ..." (or, if
# a command matches, whatever dispatch_command emits - see above) line and
# clear it. A no-op when the buffer is empty. Always emits AT MOST one
# physical stdout line (see header note): messages are RS-delimited in the
# buffer file, split back apart here, internal newlines flattened to
# spaces, then joined with " ⏎ " in arrival order.
flush_buffer() {
    [[ -s "$BUFFER_FILE" ]] || return 0
    local raw part parts out
    raw="$(cat "$BUFFER_FILE")"
    parts=()
    while [[ "$raw" == *$'\x1e'* ]]; do
        part="${raw%%$'\x1e'*}"
        parts+=("$part")
        raw="${raw#*$'\x1e'}"
    done
    [[ -n "$raw" ]] && parts+=("$raw")

    out=""
    for part in "${parts[@]}"; do
        part="${part//$'\n'/ }"
        if [[ -n "$out" ]]; then
            out="${out} ⏎ ${part}"
        else
            out="$part"
        fi
    done

    local name
    name="$(classify_command "$out")"
    if [[ -n "$name" ]]; then
        dispatch_command "$name" "$out"
    else
        emit_metric "tg-poll" "message_flushed" ""
        printf '[telegram] %s\n' "$out"
    fi
    : > "$BUFFER_FILE"
    rm -f "$BUFFER_TS_FILE"
}

# main - the poll loop itself (unchanged behavior from before this file was
# made sourceable; see the header note on why it's now wrapped in a function).
main() {
while true; do
    if [[ ! -f "$CONFIG_FILE" ]]; then
        sleep 15
        continue
    fi

    BOT_TOKEN=""
    ALLOWED_USER_ID=""
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"

    if [[ -z "${BOT_TOKEN:-}" ]]; then
        # No token yet: wait and re-check so the poller auto-starts once the
        # user finishes setup, without needing the Monitor to be re-launched.
        sleep 15
        continue
    fi

    # A quiet window may elapse with no further updates at all (the buffered
    # message really was the last one) - check before blocking on the next
    # poll so the burst still flushes promptly.
    if [[ -s "$BUFFER_FILE" && -f "$BUFFER_TS_FILE" ]]; then
        LAST_BUF_TS=$(cat "$BUFFER_TS_FILE" 2>/dev/null || echo 0)
        [[ "$LAST_BUF_TS" =~ ^[0-9]+$ ]] || LAST_BUF_TS=0
        NOW_CHECK=$(date +%s)
        if (( NOW_CHECK - LAST_BUF_TS >= REASSEMBLE_WINDOW )); then
            flush_buffer
        fi
    fi

    OFFSET=0
    if [[ -f "$OFFSET_FILE" ]]; then
        OFFSET=$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)
    fi
    [[ "$OFFSET" =~ ^[0-9]+$ ]] || OFFSET=0

    # While a burst is buffered, poll with a short timeout so the quiet
    # window above gets checked promptly; otherwise use the normal long
    # poll (cheaper - fewer wakeups when idle).
    if [[ -s "$BUFFER_FILE" ]]; then
        POLL_TIMEOUT=1
        CURL_MAXTIME=5
    else
        POLL_TIMEOUT=50
        CURL_MAXTIME=60
    fi

    RESP=$(curl -s -m "$CURL_MAXTIME" \
        "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?timeout=${POLL_TIMEOUT}&offset=${OFFSET}" \
        2>/dev/null) || { emit_metric "tg-poll" "poll_error" "curl_fail"; sleep 2; continue; }

    [[ -z "$RESP" ]] && continue

    OK=$(printf '%s' "$RESP" | jq -r '.ok // false' 2>/dev/null)
    [[ "$OK" == "true" ]] || { emit_metric "tg-poll" "poll_error" "api_not_ok"; sleep 2; continue; }

    printf '%s' "$RESP" | jq -c '.result[]?' 2>/dev/null | while IFS= read -r UPDATE; do
        UPDATE_ID=$(printf '%s' "$UPDATE" | jq -r '.update_id // empty' 2>/dev/null)
        [[ -z "$UPDATE_ID" ]] && continue

        FROM_ID=$(printf '%s' "$UPDATE" | jq -r '.message.from.id // empty' 2>/dev/null)
        TEXT=$(printf '%s' "$UPDATE" | jq -r '.message.text // empty' 2>/dev/null)

        if [[ -z "$FROM_ID" || -z "$TEXT" ]]; then
            # Nothing to buffer/emit (e.g. a non-text message) - still
            # advance the offset so it's never redelivered.
            printf '%s\n' "$((UPDATE_ID + 1))" > "$OFFSET_FILE"
            continue
        fi

        if [[ -z "${ALLOWED_USER_ID:-}" ]]; then
            printf '[telegram-setup] your user_id is %s\n' "$FROM_ID"
            printf '%s\n' "$((UPDATE_ID + 1))" > "$OFFSET_FILE"
            continue
        fi

        if [[ "$FROM_ID" == "$ALLOWED_USER_ID" ]]; then
            # Commit to the durable buffer BEFORE advancing the offset (see
            # header note): append this message (RS-delimited, not
            # newline-delimited - see header) and bump the burst's
            # last-seen timestamp so the quiet-window check above knows to
            # wait for more before flushing.
            printf '%s\x1e' "$TEXT" >> "$BUFFER_FILE"
            date +%s > "$BUFFER_TS_FILE"
        fi
        # else: unrecognized sender - silently ignored (the allowlist boundary).

        printf '%s\n' "$((UPDATE_ID + 1))" > "$OFFSET_FILE"
    done
done
}

# Only run the poll loop when executed directly - `source tg-poll.sh` (used
# by tests/) loads the functions above without starting it.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main
fi

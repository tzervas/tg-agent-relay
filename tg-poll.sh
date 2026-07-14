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
# Python poll is the **default** (epic #18 / issue #67). Opt out to shell:
#   RELAY_PYTHON_POLL=0  bash tg-poll.sh
# Falls back to this shell body if Python/package is unavailable — **noisy**
# (stderr + metrics): never silent about failure or recovery.
_PY_POLL_FALLBACK_REASON=""
_PY_POLL_FALLBACK_KIND=""  # failed | forced
if [[ "${RELAY_PYTHON_POLL:-1}" == "0" ]]; then
    _PY_POLL_FALLBACK_KIND="forced"
    _PY_POLL_FALLBACK_REASON="RELAY_PYTHON_POLL=0 (explicit shell path)"
else
    # shellcheck disable=SC1091
    [[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
    declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
    _PY_POLL_ERR=""
    _PY_POLL_RC=0
    _PY_POLL_ERR="$(relay_python -c "import tg_agent_relay.poll" 2>&1)" || _PY_POLL_RC=$?
    if [[ "$_PY_POLL_RC" -eq 0 ]]; then
        exec relay_python -m tg_agent_relay.poll "$@"
    fi
    _PY_POLL_FALLBACK_KIND="failed"
    _py_bin="${RELAY_PYTHON:-python3}"
    _PY_POLL_FALLBACK_REASON="import tg_agent_relay.poll failed (rc=${_PY_POLL_RC}, interpreter=${_py_bin})"
    if [[ -n "$_PY_POLL_ERR" ]]; then
        _PY_POLL_FALLBACK_REASON="${_PY_POLL_FALLBACK_REASON}: $(printf '%s' "$_PY_POLL_ERR" | tr '\n' ' ' | head -c 400)"
    fi
fi
CONFIG_FILE="$BRIDGE_DIR/.env"
OFFSET_FILE="$BRIDGE_DIR/.offset"
# Legacy single-buffer paths (used when multi-chat routing is off).
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

# Never-silent Python→shell recovery notice (after emit_metric is available).
# RELAY_PYTHON_FALLBACK_QUIET=1 → metric only (used by offline test suite).
if [[ -n "${_PY_POLL_FALLBACK_KIND:-}" ]]; then
    if [[ "${RELAY_PYTHON_FALLBACK_QUIET:-0}" != "1" ]]; then
        if [[ "$_PY_POLL_FALLBACK_KIND" == "failed" ]]; then
            {
                printf 'tg-poll.sh: ERROR — Python default path failed; recovering via shell.\n'
                printf '  reason:   %s\n' "$_PY_POLL_FALLBACK_REASON"
                printf '  recovery: continuing with shell tg-poll.sh (allowlist, reassembly, routing).\n'
                printf '  fix:      deploy tg_agent_relay/ + Python 3.14 (uv sync / RELAY_PYTHON=…);\n'
                printf '            set RELAY_PYTHON_POLL=0 only if shell is intentional.\n'
            } >&2
        else
            printf 'tg-poll.sh: using shell path (%s)\n' "$_PY_POLL_FALLBACK_REASON" >&2
        fi
    fi
    emit_metric "tg-poll" "python_fallback" "${_PY_POLL_FALLBACK_KIND}: ${_PY_POLL_FALLBACK_REASON}"
fi
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/routing.sh" ]] && source "$BRIDGE_DIR/lib/routing.sh"

# buffer_paths <chat_id> <thread_id> -> sets BUFFER_FILE and BUFFER_TS_FILE
# for this room. Multi-chat isolation: each chat(+thread) gets its own
# durable reassembly buffer so parallel rooms never merge.
buffer_paths() {
    local chat_id="${1:-}" thread_id="${2:-}" safe
    if [[ -z "$chat_id" ]] || ! declare -f route_has_routing_config >/dev/null 2>&1 \
        || ! route_has_routing_config; then
        BUFFER_FILE="$BRIDGE_DIR/.tg-buffer"
        BUFFER_TS_FILE="$BRIDGE_DIR/.tg-buffer-ts"
        return 0
    fi
    safe="${chat_id//[^0-9A-Za-z_-]/_}"
    if [[ -n "$thread_id" ]]; then
        safe="${safe}_t${thread_id//[^0-9A-Za-z_-]/_}"
    fi
    BUFFER_FILE="$BRIDGE_DIR/.tg-buffer.${safe}"
    BUFFER_TS_FILE="$BRIDGE_DIR/.tg-buffer-ts.${safe}"
}

# chat_is_accepted <chat_id> - 0 if this chat may receive bot traffic.
# Always accepts legacy ALLOWED_CHAT_ID; when [[chats]] configured, also
# accepts those chat_ids. Never opens the bot to unlisted groups.
chat_is_accepted() {
    local chat_id="$1"
    [[ -n "${ALLOWED_CHAT_ID:-}" && "$chat_id" == "$ALLOWED_CHAT_ID" ]] && return 0
    if declare -f route_has_routing_config >/dev/null 2>&1 && route_has_routing_config; then
        local hit
        hit="$(printf '%s' "${RELAY_CONFIG_JSON:-{}}" | jq -r --arg c "$chat_id" '
          (.chats // []) | map(select((.chat_id|tostring) == $c)) | length
        ' 2>/dev/null)"
        [[ "$hit" =~ ^[1-9] ]] && return 0
    fi
    # No routing / no ALLOWED_CHAT_ID yet: accept any chat from allowlisted user
    # (legacy go-live discovery still uses user id only).
    [[ -z "${ALLOWED_CHAT_ID:-}" ]] && return 0
    return 1
}

# deliver_to_backend <backend> <project> <text> <chat_id> <thread_id>
# Routes a flushed message to the configured backend delivery mode.
deliver_to_backend() {
    local backend="$1" project="$2" text="$3" chat_id="${4:-}" thread_id="${5:-}"
    local delivery fifo cmd_json tag filter_backend cwd

    # No backend resolved -> legacy plain stdout line.
    if [[ -z "$backend" ]]; then
        emit_metric "tg-poll" "message_flushed" "legacy"
        printf '[telegram] %s\n' "$text"
        return 0
    fi

    delivery="$(backend_cfg_get "$backend" delivery "stdout")"
    tag="$(route_inbound_tag "$backend" "$project")"
    filter_backend="${TG_POLL_BACKEND:-}"

    # Optional poller filter: only emit stdout for one backend (multi-Monitor).
    if [[ "$delivery" == "stdout" && -n "$filter_backend" && "$filter_backend" != "$backend" ]]; then
        emit_metric "tg-poll" "message_filtered" "backend=$backend want=$filter_backend"
        return 0
    fi

    case "$delivery" in
        fifo)
            fifo="$(backend_cfg_get "$backend" fifo "")"
            fifo="${fifo/#\~/$HOME}"
            if [[ -z "$fifo" ]]; then
                emit_metric "tg-poll" "deliver_skip" "backend=$backend reason=no_fifo"
                return 0
            fi
            if [[ ! -p "$fifo" ]]; then
                mkdir -p "$(dirname "$fifo")" 2>/dev/null || true
                mkfifo "$fifo" 2>/dev/null || true
            fi
            # Non-blocking-ish write: open fifo may block without a reader;
            # use timeout + background best-effort.
            if command -v timeout >/dev/null 2>&1; then
                timeout 1 bash -c "printf '%s %s\n' $(printf '%q' "$tag") $(printf '%q' "$text") > $(printf '%q' "$fifo")" 2>/dev/null \
                    || emit_metric "tg-poll" "deliver_skip" "backend=$backend reason=fifo_timeout"
            else
                printf '%s %s\n' "$tag" "$text" > "$fifo" 2>/dev/null &
            fi
            emit_metric "tg-poll" "message_delivered" "backend=$backend mode=fifo"
            ;;
        cmd)
            cwd="$(project_worktree "$project" "$backend")"
            export RELAY_TEXT="$text"
            export RELAY_BACKEND="$backend"
            export RELAY_PROJECT="$project"
            export RELAY_CHAT_ID="$chat_id"
            export RELAY_THREAD_ID="$thread_id"
            export RELAY_CWD="$cwd"
            export RELAY_MODEL
            RELAY_MODEL="$(backend_cfg_get "$backend" model "")"
            # cmd is a JSON array of argv, or a shell string.
            cmd_json="$(printf '%s' "${RELAY_CONFIG_JSON:-{}}" | jq -c --arg id "$backend" '.backends[$id].cmd // empty' 2>/dev/null)"
            if [[ -z "$cmd_json" || "$cmd_json" == "null" ]]; then
                emit_metric "tg-poll" "deliver_skip" "backend=$backend reason=no_cmd"
                return 0
            fi
            (
                [[ -n "$cwd" && -d "$cwd" ]] && cd "$cwd" 2>/dev/null || true
                if [[ "$cmd_json" == \[* ]]; then
                    # shellcheck disable=SC2046
                    eval "$(printf '%s' "$cmd_json" | jq -r 'map(@sh) | join(" ")')" </dev/null
                else
                    # string form
                    bash -lc "$(printf '%s' "$cmd_json" | jq -r '.')" </dev/null
                fi
            ) >/dev/null 2>&1 &
            emit_metric "tg-poll" "message_delivered" "backend=$backend mode=cmd"
            ;;
        *)
            # stdout (default): tagged line for Monitor consumers
            emit_metric "tg-poll" "message_flushed" "backend=$backend"
            printf '%s %s\n' "$tag" "$text"
            ;;
    esac
}

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

# flush_buffer [chat_id] [thread_id]
# Emit the buffered burst for this room (or the legacy single buffer).
# Always emits AT MOST one physical stdout line when delivery=stdout.
flush_buffer() {
    local chat_id="${1:-}" thread_id="${2:-}"
    buffer_paths "$chat_id" "$thread_id"
    [[ -s "$BUFFER_FILE" ]] || return 0
    local raw part parts out
    # Recover chat/thread from sidecar if caller didn't pass them (legacy timer).
    if [[ -z "$chat_id" && -f "${BUFFER_FILE}.meta" ]]; then
        # meta format: chat_id|thread_id
        raw_meta="$(cat "${BUFFER_FILE}.meta" 2>/dev/null || true)"
        chat_id="${raw_meta%%|*}"
        thread_id="${raw_meta#*|}"
        [[ "$thread_id" == "$raw_meta" ]] && thread_id=""
    fi
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

    local name backend project stripped match_kind
    name="$(classify_command "$out")"
    if [[ -n "$name" ]]; then
        # Relay-handled commands reply to the originating chat.
        export RELAY_CHAT_ID="$chat_id"
        export RELAY_THREAD_ID="$thread_id"
        dispatch_command "$name" "$out"
    else
        if declare -f route_resolve >/dev/null 2>&1; then
            IFS='|' read -r backend project stripped match_kind < <(route_resolve "$chat_id" "$thread_id" "$out")
            [[ -z "$stripped" && -n "$out" && "$match_kind" != "none" ]] && stripped="$out"
            if [[ "$match_kind" == "none" ]]; then
                emit_metric "tg-poll" "route_none" ""
                # Help nudge when require_prefix=true
                export RELAY_CHAT_ID="$chat_id"
                export RELAY_THREAD_ID="$thread_id"
                "$BRIDGE_DIR/relay-notify.sh" --raw "No backend matched. Prefix with @claude / @grok / @ollama, or configure [routing].default_backend." >/dev/null 2>&1 &
            else
                deliver_to_backend "$backend" "$project" "${stripped:-$out}" "$chat_id" "$thread_id"
            fi
        else
            emit_metric "tg-poll" "message_flushed" ""
            printf '[telegram] %s\n' "$out"
        fi
    fi
    : > "$BUFFER_FILE"
    rm -f "$BUFFER_TS_FILE" "${BUFFER_FILE}.meta"
}

# main - the poll loop itself (unchanged behavior from before this file was
# made sourceable; see the header note on why it's now wrapped in a function).
# flush_any_stale_buffers - quiet-window flush across all buffer files
# (legacy single buffer + per-chat multi-room buffers).
flush_any_stale_buffers() {
    local tsf buf meta chat_id thread_id LAST_BUF_TS NOW_CHECK
    NOW_CHECK=$(date +%s)
    for tsf in "$BRIDGE_DIR"/.tg-buffer-ts "$BRIDGE_DIR"/.tg-buffer-ts.*; do
        [[ -f "$tsf" ]] || continue
        LAST_BUF_TS=$(cat "$tsf" 2>/dev/null || echo 0)
        [[ "$LAST_BUF_TS" =~ ^[0-9]+$ ]] || LAST_BUF_TS=0
        if (( NOW_CHECK - LAST_BUF_TS >= REASSEMBLE_WINDOW )); then
            if [[ "$tsf" == "$BRIDGE_DIR/.tg-buffer-ts" ]]; then
                BUFFER_FILE="$BRIDGE_DIR/.tg-buffer"
                BUFFER_TS_FILE="$tsf"
                flush_buffer
            else
                # .tg-buffer-ts.<safe> -> .tg-buffer.<safe>
                buf="${tsf/.tg-buffer-ts./.tg-buffer.}"
                BUFFER_FILE="$buf"
                BUFFER_TS_FILE="$tsf"
                chat_id=""
                thread_id=""
                meta="${buf}.meta"
                if [[ -f "$meta" ]]; then
                    raw_meta="$(cat "$meta" 2>/dev/null || true)"
                    chat_id="${raw_meta%%|*}"
                    thread_id="${raw_meta#*|}"
                    [[ "$thread_id" == "$raw_meta" ]] && thread_id=""
                fi
                flush_buffer "$chat_id" "$thread_id"
            fi
        fi
    done
}

main() {
while true; do
    if [[ ! -f "$CONFIG_FILE" ]]; then
        sleep 15
        continue
    fi

    BOT_TOKEN=""
    ALLOWED_USER_ID=""
    ALLOWED_CHAT_ID=""
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
    # Reload relay.toml each loop so route table edits apply without restart.
    if declare -f load_relay_config >/dev/null 2>&1; then
        load_relay_config "$BRIDGE_DIR/relay.toml"
    fi

    if [[ -z "${BOT_TOKEN:-}" ]]; then
        # No token yet: wait and re-check so the poller auto-starts once the
        # user finishes setup, without needing the Monitor to be re-launched.
        sleep 15
        continue
    fi

    # A quiet window may elapse with no further updates at all - flush any
    # stale per-chat (or legacy) buffers before the next long poll.
    flush_any_stale_buffers

    OFFSET=0
    if [[ -f "$OFFSET_FILE" ]]; then
        OFFSET=$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)
    fi
    [[ "$OFFSET" =~ ^[0-9]+$ ]] || OFFSET=0

    # While any burst is buffered, poll with a short timeout so the quiet
    # window above gets checked promptly; otherwise use the normal long
    # poll (cheaper - fewer wakeups when idle).
    ANY_BUF=0
    for _bf in "$BRIDGE_DIR"/.tg-buffer "$BRIDGE_DIR"/.tg-buffer.*; do
        [[ -s "$_bf" ]] && [[ "$_bf" != *.meta ]] && ANY_BUF=1 && break
    done
    if (( ANY_BUF == 1 )); then
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
        CHAT_ID=$(printf '%s' "$UPDATE" | jq -r '.message.chat.id // empty' 2>/dev/null)
        THREAD_ID=$(printf '%s' "$UPDATE" | jq -r '.message.message_thread_id // empty' 2>/dev/null)

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
            # Group isolation: only accept listed chats / default DM.
            if ! chat_is_accepted "$CHAT_ID"; then
                printf '%s\n' "$((UPDATE_ID + 1))" > "$OFFSET_FILE"
                continue
            fi
            # Commit to the durable per-chat buffer BEFORE advancing offset.
            buffer_paths "$CHAT_ID" "$THREAD_ID"
            printf '%s\x1e' "$TEXT" >> "$BUFFER_FILE"
            date +%s > "$BUFFER_TS_FILE"
            printf '%s|%s\n' "$CHAT_ID" "$THREAD_ID" > "${BUFFER_FILE}.meta"
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

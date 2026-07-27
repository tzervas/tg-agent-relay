#!/bin/bash
# handlers/session.sh - Relay-handled `/session` command: manage @handle agent
# sessions from Telegram, zero model tokens. See handlers/README.md for the
# dispatch contract and docs/SESSIONS.md for the session model.
#
#   /session                    list registered sessions + delivery health
#   /session list
#   /session status <handle>    reader attached? how many messages held?
#   /session start <handle>     register the handle (and launch, if configured)
#   /session stop <handle>      unregister the handle
#
# SECURITY — why launching is opt-in and operator-defined:
#   `start` registers a session and creates its FIFO, which is inert. It runs a
#   process ONLY when the operator has set [sessions].launch_cmd in relay.toml,
#   exactly like `backends.*.delivery = "cmd"`. The command comes from the
#   operator's config, never from the Telegram message; the message contributes
#   only a handle, which must match ^[A-Za-z0-9_-]{1,32}$ and is passed through
#   the environment ($RELAY_SESSION_HANDLE) rather than interpolated into a
#   shell string. Without launch_cmd, `start` prints the Monitor command for
#   you to run — it never guesses how to start your agent.
#
#   ALLOWED_USER_ID remains the outer boundary: tg-poll only dispatches
#   commands from the allowlisted sender.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-common.sh" ]] && source "$BRIDGE_DIR/lib/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }

declare -f load_relay_config >/dev/null 2>&1 && load_relay_config "$BRIDGE_DIR/relay.toml"
declare -f cfg_get >/dev/null 2>&1 || cfg_get() { printf '%s' "${2:-}"; }

reply() {
    "$BRIDGE_DIR/relay-notify.sh" --raw "$1" >/dev/null 2>&1
    exit 0
}

# Handles name a directory entry and a FIFO, so keep them boring on purpose.
valid_handle() {
    [[ "$1" =~ ^[A-Za-z0-9_-]{1,32}$ ]]
}

# A handle argument must be the ONLY thing after the action. Validating just
# the first word would silently accept a truncation: "/session start a && rm x"
# word-splits to handle "a" and quietly registers it, and "/session start my
# handle" would create a session called "my". Neither executes anything, but
# both are wrong answers where a refusal is the right one.
handle_arg_ok() {
    [[ -z "$EXTRA" ]] && valid_handle "$HANDLE"
}

sessions_dir() {
    local d
    d="$(cfg_get '.sessions.dir' "")"
    [[ -z "$d" ]] && d="${RELAY_SESSIONS_DIR:-$BRIDGE_DIR/.sessions.d}"
    printf '%s' "${d/#\~/$HOME}"
}

# "attached" only when a real agent Monitor holds the FIFO — a keepalive is not
# delivery (see lib/fifo_agent_readers.py).
reader_state() {
    local fifo="$1" helper="$BRIDGE_DIR/lib/fifo_agent_readers.py" out
    [[ -f "$helper" ]] || { printf 'unknown'; return; }
    out="$(relay_python "$helper" "$fifo" 2>/dev/null)" || { printf 'unknown'; return; }
    case "$out" in
        *has_agent_reader=1*) printf 'attached' ;;
        *has_agent_reader=0*) printf 'ORPHAN' ;;
        *) printf 'unknown' ;;
    esac
}

spool_depth() {
    local backend="$1" out
    [[ -f "$BRIDGE_DIR/tg_agent_relay/spool.py" ]] || { printf '0'; return; }
    out="$(PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$BRIDGE_DIR" \
        relay_python -m tg_agent_relay.spool count "$backend" \
        --bridge-dir "$BRIDGE_DIR" 2>/dev/null)" || out=0
    [[ "$out" =~ ^[0-9]+$ ]] || out=0
    printf '%s' "$out"
}

session_fifo() {
    local handle="$1" reg
    reg="$(sessions_dir)/${handle}.json"
    [[ -f "$reg" ]] || return 1
    command -v jq >/dev/null 2>&1 || return 1
    jq -r '.fifo // empty' "$reg" 2>/dev/null
}

RAW="${1:-}"
# Strip the command word itself ("/session" or "session"), keep the rest.
ARGS="$(printf '%s' "$RAW" | sed -E 's#^[[:space:]]*/?session[[:space:]:]*##I')"
# shellcheck disable=SC2206
PARTS=( $ARGS )
ACTION="${PARTS[0]:-list}"
HANDLE="${PARTS[1]:-}"
EXTRA="${PARTS[*]:2}"

case "$ACTION" in
    list | "")
        SDIR="$(sessions_dir)"
        if [[ ! -d "$SDIR" ]]; then
            emit_metric "handler" "session_list" "none"
            reply "📭 No sessions registered (no $SDIR)."
        fi
        MSG="🧩 Registered sessions"$'\n'
        FOUND=0
        shopt -s nullglob
        for jf in "$SDIR"/*.json; do
            h="$(jq -r '.handle // empty' "$jf" 2>/dev/null || true)"
            f="$(jq -r '.fifo // empty' "$jf" 2>/dev/null || true)"
            [[ -n "$h" ]] || continue
            FOUND=$((FOUND + 1))
            state="$(reader_state "${f/#\~/$HOME}")"
            held="$(spool_depth "$h")"
            line="  @${h} — ${state}"
            [[ "$held" != "0" ]] && line="${line}, ${held} held for replay"
            MSG="${MSG}${line}"$'\n'
        done
        if (( FOUND == 0 )); then
            emit_metric "handler" "session_list" "none"
            reply "📭 No sessions registered."
        fi
        emit_metric "handler" "session_list" "count=$FOUND"
        reply "$MSG"
        ;;

    status)
        handle_arg_ok || reply "⚠️ Usage: /session status <handle>"
        FIFO="$(session_fifo "$HANDLE")" || reply "❓ No session registered for @${HANDLE}."
        [[ -n "$FIFO" ]] || reply "❓ No session registered for @${HANDLE}."
        STATE="$(reader_state "${FIFO/#\~/$HOME}")"
        HELD="$(spool_depth "$HANDLE")"
        MSG="🧩 @${HANDLE}"$'\n'"  fifo: ${FIFO}"$'\n'"  reader: ${STATE}"$'\n'"  held for replay: ${HELD}"
        if [[ "$STATE" == "ORPHAN" ]]; then
            MSG="${MSG}"$'\n\n'"⚠️ No agent Monitor is attached — inbound is being spooled, not delivered. Attach:"$'\n'"  adapters/backend-fifo-reader.sh ${FIFO}"
        fi
        emit_metric "handler" "session_status" "handle=$HANDLE state=$STATE held=$HELD"
        reply "$MSG"
        ;;

    start)
        handle_arg_ok \
            || reply "⚠️ Usage: /session start <handle>  (one handle: letters, digits, _ or -, max 32)"
        OUT="$("$BRIDGE_DIR/scripts/register-session.sh" --handle "$HANDLE" 2>&1)" || {
            emit_metric "handler" "session_start" "handle=$HANDLE result=register_failed"
            reply "❌ Could not register @${HANDLE}:"$'\n'"${OUT}"
        }
        FIFO="$(session_fifo "$HANDLE" || true)"
        MSG="✅ Registered @${HANDLE}"
        [[ -n "$FIFO" ]] && MSG="${MSG}"$'\n'"  fifo: ${FIFO}"

        # Launch only what the operator configured — never anything derived
        # from the Telegram message.
        LAUNCH="$(cfg_get '.sessions.launch_cmd' "")"
        if [[ -n "$LAUNCH" ]]; then
            RELAY_SESSION_HANDLE="$HANDLE" RELAY_SESSION_FIFO="${FIFO:-}" \
                nohup bash -lc "$LAUNCH" >/dev/null 2>&1 &
            emit_metric "handler" "session_start" "handle=$HANDLE result=launched"
            MSG="${MSG}"$'\n'"🚀 Launched via [sessions].launch_cmd"
        else
            emit_metric "handler" "session_start" "handle=$HANDLE result=registered"
            MSG="${MSG}"$'\n\n'"Attach the agent Monitor to receive messages:"$'\n'"  adapters/backend-fifo-reader.sh ${FIFO:-<fifo>}"$'\n\n'"(Set [sessions].launch_cmd in relay.toml to start it automatically.)"
        fi
        reply "$MSG"
        ;;

    stop)
        handle_arg_ok || reply "⚠️ Usage: /session stop <handle>"
        HELD="$(spool_depth "$HANDLE")"
        OUT="$("$BRIDGE_DIR/scripts/unregister-session.sh" --handle "$HANDLE" 2>&1)" || {
            emit_metric "handler" "session_stop" "handle=$HANDLE result=failed"
            reply "❌ Could not unregister @${HANDLE}:"$'\n'"${OUT}"
        }
        emit_metric "handler" "session_stop" "handle=$HANDLE result=ok"
        MSG="🛑 Unregistered @${HANDLE}."
        # Say so rather than let spooled messages look delivered.
        [[ "$HELD" != "0" ]] && MSG="${MSG}"$'\n'"⚠️ ${HELD} message(s) still held for replay; they will be delivered if @${HANDLE} is registered again."
        reply "$MSG"
        ;;

    *)
        reply "⚠️ Unknown action '${ACTION}'."$'\n'"Usage: /session [list | status <handle> | start <handle> | stop <handle>]"
        ;;
esac

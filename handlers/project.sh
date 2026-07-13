#!/bin/bash
# handlers/project.sh - Relay-handled /project commands for project-room binds.
#
# Usage (from tg-poll dispatch): project.sh "<flattened command text>"
# Env from poller: RELAY_CHAT_ID, RELAY_THREAD_ID
# Optional: RELAY_CHATS_OVERLAY — override path to bindings.json (default
#   $BRIDGE_DIR/.chats.d/bindings.json; same as load_relay_config).
#
# Subcommands:
#   /project | /projects | /project list  — list projects + rooms
#   /project here                         — resolve current room
#   /project bind <slug>                  — bind current chat/topic to project
#   /project unbind                       — remove overlay bind for this room
#
# Overlay contract:
#   - Written as {"chats":[{chat_id, thread_id|null, project}, ...]}
#   - Merged over static [[chats]] at load time (same chat_id|thread_id key
#     → overlay wins; other static rows kept). See lib/relay-config.sh and
#     tg_agent_relay/config.py.
#   - Negative Telegram chat_ids are stored as JSON numbers.
#   - Missing overlay is created on first successful bind; corrupt overlay
#     is never silently rewritten (operator must fix or delete).
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"
if declare -f load_relay_config >/dev/null 2>&1; then
    load_relay_config "$BRIDGE_DIR/relay.toml"
else
    cfg_get() { printf '%s' "$2"; }
fi
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-common.sh" ]] && source "$BRIDGE_DIR/lib/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/routing.sh" ]] && source "$BRIDGE_DIR/lib/routing.sh"

TEXT="${1:-}"
CHAT_ID="${RELAY_CHAT_ID:-}"
THREAD_ID="${RELAY_THREAD_ID:-}"
OVERLAY_DIR="$BRIDGE_DIR/.chats.d"
# Match load_relay_config: allow RELAY_CHATS_OVERLAY override.
if [[ -n "${RELAY_CHATS_OVERLAY:-}" ]]; then
    OVERLAY="$RELAY_CHATS_OVERLAY"
    OVERLAY_DIR="$(dirname "$OVERLAY")"
else
    OVERLAY="$OVERLAY_DIR/bindings.json"
fi

# Normalize: strip leading slash command / keyword
# e.g. "/project bind mycelium" or "project bind mycelium"
ARGS="$TEXT"
ARGS="${ARGS#/project}"
ARGS="${ARGS#project}"
ARGS="${ARGS#/projects}"
ARGS="${ARGS#projects}"
ARGS="${ARGS#"${ARGS%%[![:space:]]*}"}"  # ltrim

reply() {
    export RELAY_CHAT_ID="$CHAT_ID"
    export RELAY_THREAD_ID="$THREAD_ID"
    "$BRIDGE_DIR/relay-notify.sh" --raw "$1" >/dev/null 2>&1
}

# Telegram chat ids are signed integers (supergroups are large negatives).
_is_numeric_id() {
    [[ "${1:-}" =~ ^-?[0-9]+$ ]]
}

# Ensure overlay parent exists and file is a valid {"chats":[...]} document.
# Returns 0 with file ready to read/write; non-zero after replying on hard errors.
_ensure_overlay_writable() {
    if ! mkdir -p "$OVERLAY_DIR" 2>/dev/null; then
        reply "Cannot create overlay dir: ${OVERLAY_DIR}"
        return 1
    fi
    if [[ ! -f "$OVERLAY" ]]; then
        if ! printf '%s\n' '{"chats":[]}' > "$OVERLAY" 2>/dev/null; then
            reply "Cannot create overlay: ${OVERLAY}"
            return 1
        fi
        return 0
    fi
    if ! command -v jq >/dev/null 2>&1; then
        reply "jq required to write bindings overlay."
        return 1
    fi
    # Corrupt / non-object JSON: refuse to clobber; operator must fix.
    if ! jq -e 'type == "object"' "$OVERLAY" >/dev/null 2>&1; then
        reply "Overlay is corrupt or not a JSON object: ${OVERLAY}
Fix or delete the file, then re-run /project bind."
        return 1
    fi
    # Ensure .chats is an array (tolerate missing key).
    if ! jq -e '(.chats // []) | type == "array"' "$OVERLAY" >/dev/null 2>&1; then
        reply "Overlay .chats is not an array: ${OVERLAY}
Fix or delete the file, then re-run /project bind."
        return 1
    fi
    return 0
}

# Atomic write: jq → temp in same dir → mv. On jq failure leave overlay untouched.
# Usage: _overlay_jq_write <jq-filter> [jq --arg pairs...]
# Extra args after filter are passed to jq (e.g. --arg cid ...).
_overlay_jq_write() {
    local filter="$1"
    shift
    local tmp
    tmp="$(mktemp "${OVERLAY_DIR}/.bindings.XXXXXX")" || {
        reply "Cannot create temp file for overlay write."
        return 1
    }
    if ! jq -c "$@" "$filter" "$OVERLAY" > "$tmp" 2>/dev/null; then
        rm -f "$tmp"
        return 1
    fi
    if [[ ! -s "$tmp" ]]; then
        rm -f "$tmp"
        return 1
    fi
    if ! mv -f "$tmp" "$OVERLAY" 2>/dev/null; then
        rm -f "$tmp"
        reply "Failed to write overlay: ${OVERLAY}"
        return 1
    fi
    return 0
}

cmd="${ARGS%% *}"
rest="${ARGS#"$cmd"}"
rest="${rest#"${rest%%[![:space:]]*}"}"
[[ -z "$cmd" ]] && cmd="list"

case "$cmd" in
    list | ls | "")
        MSG="📁 Projects & rooms
"
        if command -v jq >/dev/null 2>&1; then
            PROJS="$(printf '%s' "${RELAY_CONFIG_JSON:-{}}" | jq -r '.projects // {} | keys[]?' 2>/dev/null)"
            if [[ -n "$PROJS" ]]; then
                while IFS= read -r p; do
                    [[ -z "$p" ]] && continue
                    root="$(cfg_get ".projects.\"$p\".root" "")"
                    room=""
                    if declare -f route_lookup_project >/dev/null 2>&1; then
                        room="$(route_lookup_project "$p")"
                    fi
                    MSG+=$'\n'"• ${p}"
                    [[ -n "$root" ]] && MSG+="  root=${root}"
                    if [[ -n "$room" ]]; then
                        MSG+="  room=${room}"
                    else
                        MSG+="  room=(unbound)"
                    fi
                done <<< "$PROJS"
            else
                MSG+=$'\n'"(no [projects.*] in relay.toml yet)"
            fi
            CHATS="$(printf '%s' "${RELAY_CONFIG_JSON:-{}}" | jq -r '.chats // [] | .[] | "\(.chat_id)|\(.thread_id // "")|\(.project // "")|\(.backend // "")"' 2>/dev/null)"
            if [[ -n "$CHATS" ]]; then
                MSG+=$'\n\n'"Bound rooms:"
                while IFS= read -r line; do
                    [[ -z "$line" ]] && continue
                    MSG+=$'\n'"  ${line}"
                done <<< "$CHATS"
            fi
        else
            MSG+=$'\n'"(jq unavailable)"
        fi
        MSG+=$'\n\n'"Commands: /project bind <slug> · /project unbind · /project here"
        reply "$MSG"
        emit_metric "project" "list" ""
        ;;
    here)
        if declare -f route_resolve >/dev/null 2>&1 && [[ -n "$CHAT_ID" ]]; then
            RES="$(route_resolve "$CHAT_ID" "$THREAD_ID" "ping")"
            reply "📍 This room resolves to:
backend|project|text|kind = ${RES}
chat_id=${CHAT_ID} thread_id=${THREAD_ID:-none}"
        else
            reply "📍 No routing config or chat_id unknown (legacy single DM)."
        fi
        emit_metric "project" "here" ""
        ;;
    bind)
        slug="$rest"
        slug="${slug%% *}"
        # Strip trailing whitespace / accidental CR from Telegram clients.
        slug="${slug//$'\r'/}"
        slug="${slug%"${slug##*[![:space:]]}"}"
        if [[ -z "$slug" ]]; then
            reply "Usage: /project bind <slug>
Must be sent from the target group/topic (need chat id)."
            exit 0
        fi
        if [[ -z "$CHAT_ID" ]]; then
            reply "Usage: /project bind <slug>
Must be sent from the target group/topic (need chat id)."
            exit 0
        fi
        if ! _is_numeric_id "$CHAT_ID"; then
            reply "Invalid chat_id (expected signed integer): ${CHAT_ID}"
            exit 0
        fi
        if [[ -n "$THREAD_ID" ]] && ! _is_numeric_id "$THREAD_ID"; then
            reply "Invalid thread_id (expected integer or empty): ${THREAD_ID}"
            exit 0
        fi
        if ! command -v jq >/dev/null 2>&1; then
            reply "jq required to write bindings overlay."
            exit 0
        fi
        if ! _ensure_overlay_writable; then
            exit 0
        fi
        # Upsert by chat_id|thread_id. Negative chat ids → JSON numbers via tonumber.
        # Empty thread_id → null (group-level bind, not a forum topic).
        # shellcheck disable=SC2016  # $cid/$tid/$p are jq --arg names, not shell vars
        if ! _overlay_jq_write '
          ($cid|tonumber) as $cidn
          | ($tid) as $tidraw
          | .chats = ((.chats // [])
            | map(select(
                ((.chat_id|tostring) != $cid)
                or (((.thread_id // "")|tostring) != $tidraw)
              ))
            + [{
                chat_id: $cidn,
                thread_id: (if $tidraw == "" then null else ($tidraw|tonumber) end),
                project: $p
              }]
          )
        ' --arg cid "$CHAT_ID" --arg tid "${THREAD_ID:-}" --arg p "$slug"; then
            reply "Failed to update overlay (jq error). Overlay left unchanged:
${OVERLAY}"
            exit 0
        fi
        # Reload for confirmation (merge overlay into effective config).
        if declare -f load_relay_config >/dev/null 2>&1; then
            load_relay_config "$BRIDGE_DIR/relay.toml"
        fi
        reply "✅ Bound project \`${slug}\` to this room
chat_id=${CHAT_ID} thread_id=${THREAD_ID:-none}
overlay: ${OVERLAY#"$BRIDGE_DIR"/}"
        emit_metric "project" "bind" "project=$slug"
        ;;
    unbind)
        if [[ -z "$CHAT_ID" ]]; then
            reply "Nothing to unbind (unknown chat)."
            exit 0
        fi
        if [[ ! -f "$OVERLAY" ]]; then
            reply "Nothing to unbind (no overlay file)."
            exit 0
        fi
        if ! _is_numeric_id "$CHAT_ID"; then
            reply "Invalid chat_id (expected signed integer): ${CHAT_ID}"
            exit 0
        fi
        if ! command -v jq >/dev/null 2>&1; then
            reply "jq required to update bindings overlay."
            exit 0
        fi
        if ! jq -e 'type == "object"' "$OVERLAY" >/dev/null 2>&1; then
            reply "Overlay is corrupt: ${OVERLAY}
Fix or delete the file, then re-run /project unbind."
            exit 0
        fi
        # shellcheck disable=SC2016  # $cid/$tid are jq --arg names, not shell vars
        if ! _overlay_jq_write '
          .chats = ((.chats // []) | map(select(
            ((.chat_id|tostring) != $cid)
            or (((.thread_id // "")|tostring) != $tid)
          )))
        ' --arg cid "$CHAT_ID" --arg tid "${THREAD_ID:-}"; then
            reply "Failed to update overlay (jq error). Overlay left unchanged."
            exit 0
        fi
        if declare -f load_relay_config >/dev/null 2>&1; then
            load_relay_config "$BRIDGE_DIR/relay.toml"
        fi
        reply "🗑 Unbound this room from overlay (chat_id=${CHAT_ID} thread_id=${THREAD_ID:-none})."
        emit_metric "project" "unbind" ""
        ;;
    *)
        reply "Unknown /project subcommand: ${cmd}
Try: list · here · bind <slug> · unbind"
        ;;
esac
exit 0

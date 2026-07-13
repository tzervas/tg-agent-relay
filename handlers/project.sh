#!/bin/bash
# handlers/project.sh - Relay-handled /project commands for project-room binds.
#
# Usage (from tg-poll dispatch): project.sh "<flattened command text>"
# Env from poller: RELAY_CHAT_ID, RELAY_THREAD_ID
#
# Subcommands:
#   /project | /projects | /project list  — list projects + rooms
#   /project here                         — resolve current room
#   /project bind <slug>                  — bind current chat/topic to project
#   /project unbind                       — remove overlay bind for this room
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
OVERLAY="$OVERLAY_DIR/bindings.json"

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
        if [[ -z "$slug" || -z "$CHAT_ID" ]]; then
            reply "Usage: /project bind <slug>
Must be sent from the target group/topic (need chat id)."
            exit 0
        fi
        mkdir -p "$OVERLAY_DIR" 2>/dev/null
        if [[ ! -f "$OVERLAY" ]]; then
            printf '%s\n' '{"chats":[]}' > "$OVERLAY"
        fi
        if ! command -v jq >/dev/null 2>&1; then
            reply "jq required to write bindings overlay."
            exit 0
        fi
        # Upsert by chat_id|thread_id
        TMP="$(mktemp)"
        jq -c --arg cid "$CHAT_ID" --arg tid "${THREAD_ID:-}" --arg p "$slug" '
          ($cid|tonumber) as $cidn
          | .chats = ((.chats // [])
            | map(select(
                ((.chat_id|tostring) != $cid)
                or ((.thread_id // ""|tostring) != $tid)
              ))
            + [{
                chat_id: $cidn,
                thread_id: (if $tid == "" then null else (try ($tid|tonumber) catch $tid) end),
                project: $p
              }]
          )
        ' "$OVERLAY" > "$TMP" 2>/dev/null
        if [[ -s "$TMP" ]]; then mv "$TMP" "$OVERLAY"; else rm -f "$TMP"; fi
        # Reload for confirmation
        load_relay_config "$BRIDGE_DIR/relay.toml"
        reply "✅ Bound project \`${slug}\` to this room
chat_id=${CHAT_ID} thread_id=${THREAD_ID:-none}
overlay: .chats.d/bindings.json"
        emit_metric "project" "bind" "project=$slug"
        ;;
    unbind)
        if [[ -z "$CHAT_ID" || ! -f "$OVERLAY" ]]; then
            reply "Nothing to unbind (no overlay or unknown chat)."
            exit 0
        fi
        if command -v jq >/dev/null 2>&1; then
            TMP="$(mktemp)"
            jq -c --arg cid "$CHAT_ID" --arg tid "${THREAD_ID:-}" '
              .chats = ((.chats // []) | map(select(
                ((.chat_id|tostring) != $cid)
                or ((.thread_id // ""|tostring) != $tid)
              )))
            ' "$OVERLAY" > "$TMP"
            if [[ -s "$TMP" ]]; then mv "$TMP" "$OVERLAY"; else rm -f "$TMP"; fi
        fi
        reply "🗑 Unbound this room from overlay (chat_id=${CHAT_ID})."
        emit_metric "project" "unbind" ""
        ;;
    *)
        reply "Unknown /project subcommand: ${cmd}
Try: list · here · bind <slug> · unbind"
        ;;
esac
exit 0

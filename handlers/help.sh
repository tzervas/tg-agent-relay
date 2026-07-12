#!/bin/bash
# handlers/help.sh - Relay-handled `/help` command: lists the available
# commands (both relay-handled - zero model tokens - and agent-forwarded),
# read live from relay.toml so this never drifts from what's actually
# configured. Zero model tokens itself. See handlers/README.md.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"
if declare -f load_relay_config >/dev/null 2>&1; then
    load_relay_config "$BRIDGE_DIR/relay.toml"
fi
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-common.sh" ]] && source "$BRIDGE_DIR/lib/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }

RELAY_LINES=""
FORWARD_LINES=""

if command -v jq >/dev/null 2>&1 && [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
    ENTRIES="$(printf '%s' "$RELAY_CONFIG_JSON" | jq -c '.commands // {} | to_entries[]?' 2>/dev/null)"
    while IFS= read -r ENTRY; do
        [[ -z "$ENTRY" ]] && continue
        NAME="$(printf '%s' "$ENTRY" | jq -r '.key')"
        MODE="$(printf '%s' "$ENTRY" | jq -r '.value.mode // "forward"')"
        SLASH="$(printf '%s' "$ENTRY" | jq -r '.value.slash // empty')"
        KEYWORD="$(printf '%s' "$ENTRY" | jq -r '.value.keyword // empty')"
        FORM="$SLASH"
        [[ -z "$FORM" ]] && FORM="$KEYWORD"
        [[ -z "$FORM" ]] && FORM="$NAME"
        if [[ "$MODE" == "relay" ]]; then
            RELAY_LINES="${RELAY_LINES}  ${FORM} — ${NAME} (zero model tokens)"$'\n'
        else
            FORWARD_LINES="${FORWARD_LINES}  ${FORM} — ${NAME} (forwarded to the agent)"$'\n'
        fi
    done <<< "$ENTRIES"
fi

MSG="🧭 TG Agent Relay — help
"
if [[ -n "$RELAY_LINES" ]]; then
    MSG="${MSG}
Relay-handled (zero model tokens):
${RELAY_LINES}"
fi
if [[ -n "$FORWARD_LINES" ]]; then
    MSG="${MSG}
Forwarded to the agent:
${FORWARD_LINES}"
fi
if [[ -z "$RELAY_LINES" && -z "$FORWARD_LINES" ]]; then
    MSG="${MSG}
(no [commands.*] configured in relay.toml — every message forwards as plain text)"
fi
MSG="${MSG}
Any other message is forwarded to the agent as-is."

emit_metric "dashboard" "help_reply" ""
"$BRIDGE_DIR/relay-notify.sh" --raw "$MSG" >/dev/null 2>&1
exit 0

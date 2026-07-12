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
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$BRIDGE_DIR/.env"
OFFSET_FILE="$BRIDGE_DIR/.offset"

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

    OFFSET=0
    if [[ -f "$OFFSET_FILE" ]]; then
        OFFSET=$(cat "$OFFSET_FILE" 2>/dev/null || echo 0)
    fi
    [[ "$OFFSET" =~ ^[0-9]+$ ]] || OFFSET=0

    RESP=$(curl -s -m 60 \
        "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?timeout=50&offset=${OFFSET}" \
        2>/dev/null) || { sleep 2; continue; }

    [[ -z "$RESP" ]] && continue

    OK=$(printf '%s' "$RESP" | jq -r '.ok // false' 2>/dev/null)
    [[ "$OK" == "true" ]] || { sleep 2; continue; }

    printf '%s' "$RESP" | jq -c '.result[]?' 2>/dev/null | while IFS= read -r UPDATE; do
        UPDATE_ID=$(printf '%s' "$UPDATE" | jq -r '.update_id // empty' 2>/dev/null)
        [[ -z "$UPDATE_ID" ]] && continue

        # Persist the offset past this update immediately (never re-deliver on restart).
        printf '%s\n' "$((UPDATE_ID + 1))" > "$OFFSET_FILE"

        FROM_ID=$(printf '%s' "$UPDATE" | jq -r '.message.from.id // empty' 2>/dev/null)
        TEXT=$(printf '%s' "$UPDATE" | jq -r '.message.text // empty' 2>/dev/null)

        [[ -z "$FROM_ID" ]] && continue
        [[ -z "$TEXT" ]] && continue

        if [[ -z "${ALLOWED_USER_ID:-}" ]]; then
            printf '[telegram-setup] your user_id is %s\n' "$FROM_ID"
            continue
        fi

        if [[ "$FROM_ID" == "$ALLOWED_USER_ID" ]]; then
            printf '[telegram] %s\n' "$TEXT"
        fi
        # else: unrecognized sender - silently ignored (the allowlist boundary).
    done
done

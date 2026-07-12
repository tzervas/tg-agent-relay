#!/bin/bash
# go-live.sh - Activation script for the Telegram <-> Claude Code bridge.
#
# 1. Verifies BOT_TOKEN is set in .env (else tells you what to do).
# 2. Validates the token via Telegram's getMe (prints the bot's @username).
# 3. If ALLOWED_USER_ID / ALLOWED_CHAT_ID are unset, checks for an incoming
#    message and auto-resolves + writes the sender's id into .env.
# 4. Sends a "bridge live" confirmation DM.
#
# Idempotent: safe to re-run any time (e.g. after adding the token).
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$BRIDGE_DIR/.env"

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "No .env found at $CONFIG_FILE" >&2
    exit 1
fi

# shellcheck disable=SC1090
source "$CONFIG_FILE"

if [[ -z "${BOT_TOKEN:-}" ]]; then
    echo "no token yet, add it to .env"
    exit 1
fi

echo "Validating token..."
ME_RESP=$(curl -s -m 10 "https://api.telegram.org/bot${BOT_TOKEN}/getMe")
ME_OK=$(printf '%s' "$ME_RESP" | jq -r '.ok // false' 2>/dev/null)

if [[ "$ME_OK" != "true" ]]; then
    echo "Token validation failed - getMe did not return ok:true. Check BOT_TOKEN in .env." >&2
    exit 1
fi

BOT_USERNAME=$(printf '%s' "$ME_RESP" | jq -r '.result.username // "unknown"' 2>/dev/null)
echo "Token valid - bot is @${BOT_USERNAME}"

if [[ -z "${ALLOWED_USER_ID:-}" || -z "${ALLOWED_CHAT_ID:-}" ]]; then
    echo "ALLOWED_USER_ID/ALLOWED_CHAT_ID not set - checking for an incoming message..."
    UPD_RESP=$(curl -s -m 10 "https://api.telegram.org/bot${BOT_TOKEN}/getUpdates?timeout=1")
    UPD_OK=$(printf '%s' "$UPD_RESP" | jq -r '.ok // false' 2>/dev/null)

    if [[ "$UPD_OK" != "true" ]]; then
        echo "getUpdates failed while resolving your user id." >&2
        exit 1
    fi

    FOUND_ID=$(printf '%s' "$UPD_RESP" | jq -r '[.result[]?.message.from.id] | last // empty' 2>/dev/null)
    LAST_UPDATE_ID=$(printf '%s' "$UPD_RESP" | jq -r '[.result[]?.update_id] | last // empty' 2>/dev/null)

    if [[ -z "$FOUND_ID" ]]; then
        echo "No incoming messages yet. Send your bot any message (e.g. \"hi\"), then re-run this script." >&2
        exit 1
    fi

    echo "Found sender id ${FOUND_ID} - writing to .env"
    sed -i "s/^ALLOWED_USER_ID=.*/ALLOWED_USER_ID=${FOUND_ID}/" "$CONFIG_FILE"
    sed -i "s/^ALLOWED_CHAT_ID=.*/ALLOWED_CHAT_ID=${FOUND_ID}/" "$CONFIG_FILE"
    chmod 600 "$CONFIG_FILE"
    ALLOWED_USER_ID="$FOUND_ID"
    ALLOWED_CHAT_ID="$FOUND_ID"

    # Advance the poll offset past this update so tg-poll.sh doesn't re-deliver it.
    if [[ -n "$LAST_UPDATE_ID" ]]; then
        echo "$((LAST_UPDATE_ID + 1))" > "$BRIDGE_DIR/.offset"
    fi
fi

echo "Sending live confirmation DM to chat ${ALLOWED_CHAT_ID}..."
"$BRIDGE_DIR/tg-send.sh" "🟢 Bridge live — Claude Code ↔ Telegram connected"
echo "Done. (If you don't see the DM, double-check ALLOWED_CHAT_ID in .env.)"

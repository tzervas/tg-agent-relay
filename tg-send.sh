#!/bin/bash
# tg-send.sh - Outbound status pinger: Claude Code -> Telegram phone (0 model tokens).
#
# Invoked by Claude Code hooks (SubagentStop, Notification) via hook-notify.sh, or
# directly/manually. Reads .env; SILENTLY NO-OPS (exit 0, no output, no error)
# if BOT_TOKEN is unset/empty, so hooks are harmless before setup.
#
# Usage:
#   tg-send.sh "message text"
#   echo "message text" | tg-send.sh
#
# Security: the token lives only in .env (mode 0600); this script never
# echoes or logs it. Outbound-only (a plain curl POST) - no listening port.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$BRIDGE_DIR/.env"
LAST_MSG_FILE="$BRIDGE_DIR/.last-sent"

# Message text: args take priority over stdin.
if [[ $# -gt 0 ]]; then
    MSG="$*"
else
    MSG="$(cat)"
fi

# Nothing to send.
[[ -z "$MSG" ]] && exit 0

# No config yet -> silent no-op.
[[ -f "$CONFIG_FILE" ]] || exit 0

# shellcheck disable=SC1090
source "$CONFIG_FILE"

# No token yet -> silent no-op (this is the "harmless before setup" contract).
[[ -z "${BOT_TOKEN:-}" ]] && exit 0
[[ -z "${ALLOWED_CHAT_ID:-}" ]] && exit 0

# Light rate-limit/dedup: skip an identical message sent within the last 10s,
# so a hook storm (e.g. several subagents finishing at once) can't flood the chat.
NOW=$(date +%s)
if [[ -f "$LAST_MSG_FILE" ]]; then
    LAST_LINE=$(head -n1 "$LAST_MSG_FILE" 2>/dev/null || true)
    LAST_TS="${LAST_LINE%%|*}"
    LAST_TEXT="${LAST_LINE#*|}"
    if [[ "$LAST_TEXT" == "$MSG" && "$LAST_TS" =~ ^[0-9]+$ ]] && (( NOW - LAST_TS < 10 )); then
        exit 0
    fi
fi

curl -s -m 10 -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${ALLOWED_CHAT_ID}" \
    --data-urlencode "text=${MSG}" \
    >/dev/null 2>&1 || true

printf '%s|%s\n' "$NOW" "$MSG" > "$LAST_MSG_FILE" 2>/dev/null || true

exit 0

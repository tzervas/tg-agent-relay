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
#
# Auto-pagination: a message over TG_PAGE_SIZE chars (default 3500, safely
# under Telegram's 4096 hard cap once the "[k/n]\n" prefix + multibyte margin
# are accounted for) is split on line/paragraph boundaries into multiple
# messages, each prefixed "[k/n]", and sent in order with a short
# TG_PAGE_DELAY between sends so Telegram preserves ordering. A single line
# that alone exceeds the page size is hard-split as a fallback. A short
# message still sends as exactly one message, with no prefix.
#
# Config fallback order (backward-compatible): TG_PAGE_SIZE/TG_PAGE_DELAY env
# vars (if set) > relay.toml [general].page_size/page_delay (if a relay.toml
# is present) > the hardcoded defaults below. No relay.toml -> behavior is
# identical to before relay.toml existed.
#
# Optional local TTS voice notes (lib/tts.sh, relay.toml [tts]): OFF by
# default (mode = "off") -> byte-identical to pre-TTS behavior, no
# relay.toml needed. When [tts].mode is "text+voice" or "voice-only" AND
# the message is a single page within [tts].max_chars AND a local engine
# (piper or espeak-ng) is available, a voice note is generated locally (no
# external TTS API) and sent via sendVoice - see lib/tts.sh's header for
# the full skip-graceful contract. "voice-only" still sends the text
# whenever TTS is unavailable/too-long - a message is never dropped
# outright.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$BRIDGE_DIR/.env"
LAST_MSG_FILE="$BRIDGE_DIR/.last-sent"

# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"
if declare -f load_relay_config >/dev/null 2>&1; then
    load_relay_config "$BRIDGE_DIR/relay.toml"
else
    cfg_get() { printf '%s' "$2"; }  # lib missing (shouldn't happen) -> default-only shim
fi
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-common.sh" ]] && source "$BRIDGE_DIR/lib/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }  # lib missing -> no-op shim
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/tts.sh" ]] && source "$BRIDGE_DIR/lib/tts.sh"
declare -f tts_send_voice >/dev/null 2>&1 || tts_send_voice() { return 1; }  # lib missing -> unavailable shim

# Telegram's hard cap is 4096 chars; stay safely under it.
PAGE_SIZE="${TG_PAGE_SIZE:-$(cfg_get '.general.page_size' 3500)}"
[[ "$PAGE_SIZE" =~ ^[0-9]+$ ]] || PAGE_SIZE=3500
# Delay (seconds, may be fractional) between sequential page sends.
PAGE_DELAY="${TG_PAGE_DELAY:-$(cfg_get '.general.page_delay' 0.4)}"

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
# so a hook storm (e.g. several subagents finishing at once) can't flood the
# chat. Keyed on the full, pre-split $MSG, so a multi-page message is deduped
# (or not) as one unit - never partially.
NOW=$(date +%s)
if [[ -f "$LAST_MSG_FILE" ]]; then
    LAST_LINE=$(head -n1 "$LAST_MSG_FILE" 2>/dev/null || true)
    LAST_TS="${LAST_LINE%%|*}"
    LAST_TEXT="${LAST_LINE#*|}"
    if [[ "$LAST_TEXT" == "$MSG" && "$LAST_TS" =~ ^[0-9]+$ ]] && (( NOW - LAST_TS < 10 )); then
        exit 0
    fi
fi

# --- Split $MSG into PAGES[] on line boundaries; only hard-split a single
# --- line that alone exceeds PAGE_SIZE. A message within PAGE_SIZE is a
# --- single unmodified page (today's exact behavior). ---
PAGES=()
if (( ${#MSG} <= PAGE_SIZE )); then
    PAGES=("$MSG")
else
    CUR=""
    while IFS= read -r LINE || [[ -n "$LINE" ]]; do
        if [[ -n "$CUR" ]]; then
            CAND="${CUR}"$'\n'"${LINE}"
        else
            CAND="$LINE"
        fi

        if (( ${#CAND} <= PAGE_SIZE )); then
            CUR="$CAND"
            continue
        fi

        # CAND overflowed the page: flush whatever we already had.
        [[ -n "$CUR" ]] && PAGES+=("$CUR")
        CUR=""

        if (( ${#LINE} <= PAGE_SIZE )); then
            CUR="$LINE"
        else
            # A single line longer than PAGE_SIZE: hard-split it (fallback).
            REST="$LINE"
            while (( ${#REST} > PAGE_SIZE )); do
                PAGES+=("${REST:0:PAGE_SIZE}")
                REST="${REST:PAGE_SIZE}"
            done
            CUR="$REST"
        fi
    done <<< "$MSG"
    [[ -n "$CUR" ]] && PAGES+=("$CUR")
    (( ${#PAGES[@]} == 0 )) && PAGES=("$MSG")
fi

TOTAL=${#PAGES[@]}

# --- Optional local TTS voice note (relay.toml [tts]; default mode="off"
# --- -> both blocks below are no-ops and this is byte-identical to
# --- pre-TTS behavior). See lib/tts.sh's header for the engine/transcode/
# --- send pipeline and its skip-graceful contract.
TTS_MODE="$(cfg_get '.tts.mode' 'off')"
case "$TTS_MODE" in
    text+voice|voice-only) ;;
    *) TTS_MODE="off" ;;  # "off", unset, or any unrecognized value -> off
esac

# Eligible only for a single-page message (a paginated long message is,
# by construction, over PAGE_SIZE and so also over the much smaller
# max_chars - but the length check is explicit here too, never implicit)
# within [tts].max_chars (default 600) - long reports/dashboards stay
# text-only, per design.
TTS_ELIGIBLE=0
if [[ "$TTS_MODE" != "off" && "$TOTAL" -eq 1 ]]; then
    TTS_MAX_CHARS="$(cfg_get '.tts.max_chars' 600)"
    [[ "$TTS_MAX_CHARS" =~ ^[0-9]+$ ]] || TTS_MAX_CHARS=600
    (( ${#MSG} <= TTS_MAX_CHARS )) && TTS_ELIGIBLE=1
fi

# "voice-only": try the voice note FIRST; only skip the text loop below on
# an actual successful send. Unavailable engine / conversion failure /
# send failure / ineligible (off, too long, paginated) all fall through
# to the ordinary text send - a message is never sent as nothing.
VOICE_SENT=0
if [[ "$TTS_MODE" == "voice-only" && "$TTS_ELIGIBLE" -eq 1 ]]; then
    tts_send_voice "$BOT_TOKEN" "$ALLOWED_CHAT_ID" "$MSG" && VOICE_SENT=1
fi

if [[ "$TTS_MODE" != "voice-only" || "$VOICE_SENT" -eq 0 ]]; then
    IDX=0
    for PAGE in "${PAGES[@]}"; do
        IDX=$((IDX + 1))
        if (( TOTAL > 1 )); then
            SEND_TEXT="[${IDX}/${TOTAL}]"$'\n'"${PAGE}"
        else
            SEND_TEXT="$PAGE"
        fi

        curl -s -m 10 -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${ALLOWED_CHAT_ID}" \
            --data-urlencode "text=${SEND_TEXT}" \
            >/dev/null 2>&1 || true

        (( IDX < TOTAL )) && sleep "$PAGE_DELAY"
    done
fi

# "text+voice": text already sent above (unchanged path); now send the
# voice note too, best-effort (never blocks/reverts the already-sent text
# on a TTS failure).
if [[ "$TTS_MODE" == "text+voice" && "$TTS_ELIGIBLE" -eq 1 ]]; then
    tts_send_voice "$BOT_TOKEN" "$ALLOWED_CHAT_ID" "$MSG" || true
fi

printf '%s|%s\n' "$NOW" "$MSG" > "$LAST_MSG_FILE" 2>/dev/null || true

emit_metric "tg-send" "send" "pages=${TOTAL}"

exit 0

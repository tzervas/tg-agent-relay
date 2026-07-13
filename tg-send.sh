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
#
# Hook audio (`[tts].hook_voice`, default true): automated hook/notification
# pings (routed here via adapters/claude-code.sh -> relay-notify.sh, which
# export/pass through TG_SEND_SOURCE=hook) get a voice read-through even
# when long or paginated, where an ordinary direct send would stay
# text-only - long reports are exactly the case a hook ping usually is.
# The SPOKEN text is capped at `[tts].hook_voice_max_chars` (default 1500,
# higher than the ordinary `max_chars`) - a sensible read-through, not the
# whole report; the TEXT send always carries the full, unabridged message
# regardless (voice is strictly additive - never-silent: text is never
# skipped for a hook ping, even in `mode = "voice-only"`). Set
# `hook_voice = false` to restore the pre-v0.5.1 hook-is-text-only shape.
#
# Serialized send queue + ordering (`[general].send_interval_ms`, default
# 350): every send (dedup check through the final metric write) runs under
# an exclusive `flock` on `.tg-send.lock`, so concurrent invocations (a
# burst of hook events) queue up and send one at a time, each send's text
# pages + voice note completing before the next begins - never
# interleaved, never reordered by a network race. The lock is held for
# `send_interval_ms` after each send finishes before the next invocation
# can proceed, adding a small, configurable, accepted delay so Telegram's
# own delivery also preserves order. `flock` missing -> skip-graceful,
# unserialized (logged once, never a hard failure).
#
# Structured formatting (lib/format.sh, relay.toml [format]): ON by
# default (headline v0.3.0 feature - phone-readable messages instead of a
# wall of text) - dynamic soft-wrap, bolded section headers, code boxes,
# quotes, light emphasis, sent via parse_mode=HTML. [format].enabled=false
# or parse_mode="none" -> byte-identical to pre-format-layer plain text.
# See lib/format.sh's header for the full input-markup convention and the
# never-silent fallback (a malformed render, or a Telegram-side HTML parse
# rejection, retries ONCE as plain text - a message is never dropped nor
# sent with broken markup). The "[k/n]" pagination header is bolded when
# formatting is active, and stays exactly "[k/n]" plain text otherwise.
#
# Host-highlighted code DOCUMENTS (lib/code_highlight.sh, relay.toml
# [code_highlight]): the v0.3.0 inline `<pre><code class="language-X">` box
# above is ALWAYS sent for every fenced block, unchanged - Telegram TEXT
# has no color at all (a fixed HTML entity set; <pre>/<code> can't even
# nest <b>/<i>), so that's the best a chat bubble alone can do. With
# `[code_highlight] mode = "html-doc"`, each fenced block is ADDITIONALLY
# rendered host-side (pygments -> a self-contained HTML document, all CSS
# inlined) and sent via sendDocument - opened in the phone's browser, it
# shows real per-token colors on any device, no local highlighter needed,
# and the code stays selectable/copyable. `mode = "inline-only"` (the
# default) or `"off"` skip this entirely - just the inline box, as always.
# Never-silent: pygments absent, a render error, or a block over
# [code_highlight].max_lines just means no document is sent for that one
# block - the inline box already carries the code either way, so nothing
# is ever dropped. (Also see lib/format.sh's `_fmt_render_code_block` for
# the separate, unconditional `[code_highlight].myc_inline_lang` alias
# that makes a myc/mycelium inline box actually light up on stock
# Telegram clients today.)
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
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/format.sh" ]] && source "$BRIDGE_DIR/lib/format.sh"
declare -f format_message >/dev/null 2>&1 || format_message() { FMT_TEXT="$1"; FMT_PARSE_MODE=""; }  # lib missing -> passthrough shim
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/code_highlight.sh" ]] && source "$BRIDGE_DIR/lib/code_highlight.sh"
# lib missing -> no-op shim (nothing queued; the inline code box from
# format_message() above is entirely unaffected either way).
if ! declare -f _img_process_message >/dev/null 2>&1; then
    # shellcheck disable=SC2034  # IMG_PENDING_* are consumed further down in this script
    _img_process_message() { IMG_PENDING_DOCS=(); IMG_PENDING_LANGS=(); IMG_PENDING_CAPTIONS=(); }
    img_send_pending() { :; }
fi

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

# --- Serialized send queue (guaranteed ordering) ----------------------------
# Concurrent tg-send.sh invocations (e.g. several SubagentStop hooks firing
# within the same second) each POST to Telegram independently; network
# scheduling gives no ordering guarantee across separate curl processes, so
# messages can arrive out of order. Fix: an exclusive flock on a lockfile
# serializes the ENTIRE remainder of this script (dedup check through the
# final send + metric) across every concurrent invocation, one at a time -
# so a send (its text pages + its voice note) always completes before the
# next invocation's send begins. Acquisition order under contention is a
# kernel-level FIFO wait queue on Linux, so invocations serialize in
# (very close to) the order they were fired - and the maintainer has
# explicitly accepted a slight added delay to guarantee this.
#
# Skip-graceful: if `flock` isn't installed, sending proceeds unserialized
# (today's behavior) rather than failing - logged once so the gap is never
# silent.
LOCK_FILE="${TG_SEND_LOCK_FILE:-$BRIDGE_DIR/.tg-send.lock}"
SEND_INTERVAL_MS="${TG_SEND_INTERVAL_MS:-$(cfg_get '.general.send_interval_ms' 350)}"
[[ "$SEND_INTERVAL_MS" =~ ^[0-9]+$ ]] || SEND_INTERVAL_MS=350
HAVE_FLOCK=0
if command -v flock >/dev/null 2>&1; then
    HAVE_FLOCK=1
    # shellcheck disable=SC2261
    exec 200>"$LOCK_FILE"
    flock -x 200
else
    emit_metric "queue" "flock_unavailable" "serialized send ordering skipped - install util-linux flock"
fi

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

# This send's origin: adapters/claude-code.sh exports TG_SEND_SOURCE=hook
# before invoking relay-notify.sh, which is inherited straight through to
# this process (a real environment variable, not a shell-local one) - no
# extra plumbing needed. Empty/unset (a direct/manual tg-send.sh call, or
# any other harness that doesn't set it) means "not a hook".
SEND_SOURCE="${TG_SEND_SOURCE:-}"
IS_HOOK=0
[[ "$SEND_SOURCE" == "hook" ]] && IS_HOOK=1

# hook_voice: automated hook/notification pings (SubagentStop, Notification,
# ...) get a voice read-through too, even when long/paginated - the
# maintainer's explicit ask (they were getting text-only for every hook
# ping while direct tg-send.sh calls got voice, because those tend to be
# short/single-page and hook pings routinely are not). Defaults to true so
# turning TTS on gets hook coverage by construction; set
# `[tts] hook_voice = false` to restore the old hook-is-always-text-only
# shape.
HOOK_VOICE="$(cfg_get '.tts.hook_voice' 'true')"
case "$HOOK_VOICE" in
    true | 1 | yes | on) HOOK_VOICE=1 ;;
    *) HOOK_VOICE=0 ;;
esac
# Cap on how much of a long/paginated hook message is actually SPOKEN - a
# "sensible read-through" (the maintainer's phrasing), not the full report.
# Higher than [tts].max_chars (which still gates ordinary direct sends)
# because a hook ping is exactly the case that needed relaxing. The TEXT
# send is completely unaffected either way - this only bounds what goes
# into tts_send_voice.
HOOK_VOICE_MAX_CHARS="$(cfg_get '.tts.hook_voice_max_chars' 1500)"
[[ "$HOOK_VOICE_MAX_CHARS" =~ ^[0-9]+$ ]] || HOOK_VOICE_MAX_CHARS=1500

TTS_MAX_CHARS="$(cfg_get '.tts.max_chars' 600)"
[[ "$TTS_MAX_CHARS" =~ ^[0-9]+$ ]] || TTS_MAX_CHARS=600

TTS_ELIGIBLE=0
TTS_VOICE_TEXT="$MSG"
if [[ "$TTS_MODE" != "off" ]]; then
    if (( IS_HOOK == 1 && HOOK_VOICE == 1 )); then
        # Hook ping: eligible regardless of pagination/length - never
        # silently skip voice just because the ping was long. Only the
        # SPOKEN text is capped; every text page still goes out unabridged.
        if (( ${#MSG} > 0 )); then
            TTS_ELIGIBLE=1
            if (( ${#MSG} > HOOK_VOICE_MAX_CHARS )); then
                TTS_VOICE_TEXT="${MSG:0:HOOK_VOICE_MAX_CHARS}"
                emit_metric "tts" "hook_voice_truncated" "chars=${#MSG} max=${HOOK_VOICE_MAX_CHARS}"
            fi
        fi
    elif [[ "$TOTAL" -eq 1 ]]; then
        # Direct/manual send (or a hook with hook_voice disabled): the
        # original, unrelaxed rule - single page, within max_chars.
        (( ${#MSG} <= TTS_MAX_CHARS )) && TTS_ELIGIBLE=1
    fi
fi

# "voice-only": try the voice note FIRST; only skip the text loop below on
# an actual successful send. Unavailable engine / conversion failure /
# send failure / ineligible (off, too long, paginated) all fall through
# to the ordinary text send - a message is never sent as nothing.
#
# Hook pings NEVER take this early/skip-text path, even in "voice-only"
# mode: the never-silent contract for an automated ping is stronger than
# usual - text ALWAYS sends, voice is purely additive (see the final
# text+voice/hook block below the send loop). A direct/manual "voice-only"
# send keeps its exact original shape (skip text on a successful voice
# send).
VOICE_SENT=0
if [[ "$TTS_MODE" == "voice-only" && "$TTS_ELIGIBLE" -eq 1 && "$IS_HOOK" -eq 0 ]]; then
    tts_send_voice "$BOT_TOKEN" "$ALLOWED_CHAT_ID" "$TTS_VOICE_TEXT" && VOICE_SENT=1
fi

# _tg_post_send_message <text> <parse_mode> - one sendMessage POST.
# <parse_mode> "" means no parse_mode param at all (plain text, today's
# exact request shape). Returns 0 iff Telegram's response says "ok":true -
# never trusts curl's own exit code alone (that only proves the HTTP round
# trip happened, not that Telegram accepted the message).
_tg_post_send_message() {
    local text="$1" parse_mode="$2" resp
    if [[ -n "$parse_mode" ]]; then
        resp="$(curl -s -m 10 -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${ALLOWED_CHAT_ID}" \
            --data-urlencode "text=${text}" \
            --data-urlencode "parse_mode=${parse_mode}" \
            2>/dev/null)"
    else
        resp="$(curl -s -m 10 -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            --data-urlencode "chat_id=${ALLOWED_CHAT_ID}" \
            --data-urlencode "text=${text}" \
            2>/dev/null)"
    fi
    [[ "$resp" == *'"ok":true'* ]]
}

if [[ "$TTS_MODE" != "voice-only" || "$VOICE_SENT" -eq 0 ]]; then
    IDX=0
    for PAGE in "${PAGES[@]}"; do
        IDX=$((IDX + 1))

        # _img_process_message populates the IMG_PENDING_* job queue
        # (globals - same "plain statement, not $(...)" rule as
        # format_message, see lib/code_highlight.sh's header) for any
        # fenced block that should ALSO go out as a highlighted HTML
        # document. It is purely READ-ONLY with respect to $PAGE - the
        # text below is completely unaffected by it either way.
        _img_process_message "$PAGE"

        # format_message sets FMT_TEXT/FMT_PARSE_MODE as globals - see
        # lib/format.sh's header for why this is NOT `x="$(format_message ...)"`
        # (command substitution's subshell would drop the second value).
        format_message "$PAGE"

        # Plain fallback text (today's exact pre-format shape) - always
        # computed, used only if the formatted send fails (never-silent
        # retry-as-plain-text, see lib/format.sh's header).
        if (( TOTAL > 1 )); then
            PLAIN_SEND_TEXT="[${IDX}/${TOTAL}]"$'\n'"${PAGE}"
        else
            PLAIN_SEND_TEXT="$PAGE"
        fi

        if [[ -n "$FMT_PARSE_MODE" ]]; then
            if (( TOTAL > 1 )); then
                SEND_TEXT="<b>[${IDX}/${TOTAL}]</b>"$'\n'"${FMT_TEXT}"
            else
                SEND_TEXT="$FMT_TEXT"
            fi
        else
            # Formatting off/disabled -> FMT_TEXT == PAGE unchanged, so this
            # is byte-for-byte the pre-format-layer request.
            SEND_TEXT="$PLAIN_SEND_TEXT"
        fi

        if ! _tg_post_send_message "$SEND_TEXT" "$FMT_PARSE_MODE"; then
            if [[ -n "$FMT_PARSE_MODE" ]]; then
                emit_metric "format" "send_fallback" "page=${IDX}/${TOTAL} formatted send failed, retrying as plain text"
                _tg_post_send_message "$PLAIN_SEND_TEXT" "" || true
            fi
            # else: already plain - nothing more to try (matches the
            # original "|| true" swallow-and-move-on behavior).
        fi

        # Any code-highlight document jobs queued for THIS page
        # (best-effort, after the main text - which already carries the
        # v0.3.0 inline code box - has gone out). A no-op (returns
        # immediately) whenever [code_highlight] mode != "html-doc" or no
        # fence was found/rendered.
        img_send_pending "$BOT_TOKEN" "$ALLOWED_CHAT_ID"

        (( IDX < TOTAL )) && sleep "$PAGE_DELAY"
    done
fi

# Additive voice note: text has already sent above (unchanged path); now
# send the voice note too, best-effort (never blocks/reverts the
# already-sent text on a TTS failure). Fires for "text+voice" (any
# source, as before) OR any eligible hook ping that hasn't already sent
# its voice note (hooks never take the voice-only pre-send path above -
# see VOICE_SENT's guard - so this is where a hook's voice note actually
# goes out, in EVERY tts mode except "off").
if [[ "$TTS_ELIGIBLE" -eq 1 && "$VOICE_SENT" -eq 0 ]] \
    && { [[ "$TTS_MODE" == "text+voice" ]] || [[ "$IS_HOOK" -eq 1 ]]; }; then
    tts_send_voice "$BOT_TOKEN" "$ALLOWED_CHAT_ID" "$TTS_VOICE_TEXT" || true
fi

printf '%s|%s\n' "$NOW" "$MSG" > "$LAST_MSG_FILE" 2>/dev/null || true

emit_metric "tg-send" "send" "pages=${TOTAL}"

# Hold the serialization lock for the configured inter-send interval before
# releasing (fd 200 closes at process exit) - this is what actually
# enforces a minimum gap between one send finishing and the next
# beginning, so Telegram sees sends spaced out even under a hook storm.
# No-op (0s) when flock isn't available or the interval is 0.
if (( HAVE_FLOCK == 1 && SEND_INTERVAL_MS > 0 )); then
    _interval_s="$(awk -v ms="$SEND_INTERVAL_MS" 'BEGIN { printf "%.3f", ms / 1000 }' 2>/dev/null || true)"
    [[ -n "$_interval_s" ]] && sleep "$_interval_s" 2>/dev/null
fi

exit 0

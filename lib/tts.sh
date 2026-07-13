#!/bin/bash
# lib/tts.sh - Self-hosted, local text-to-speech: text -> WAV -> OGG/OPUS ->
# Telegram sendVoice. Sourced by tg-send.sh (never executed directly, no
# shebang execution path - same convention as lib/relay-common.sh).
#
# NO external TTS API - everything runs on this host: piper (preferred, a
# small local neural TTS engine + a downloaded .onnx voice model) or
# espeak-ng (fallback, apt-installable, zero extra config, robotic but
# always available) synthesize a WAV; ffmpeg re-encodes it to OGG/OPUS
# (what Telegram's sendVoice expects) before the multipart upload. See
# SETUP.md's "Voice messages (TTS)" section for how to install either.
#
# SKIP-GRACEFUL, by design (this repo's "harmless before setup" contract -
# see tg-send.sh's header): if no engine is installed, or ffmpeg is
# missing (WAV is then sent via sendAudio instead of transcoding), or
# synthesis/conversion/the network call fails for any reason,
# tts_send_voice returns 1 and the caller (tg-send.sh) falls back to
# text - the text message is NEVER blocked or delayed by a TTS failure.
# Every skip/send outcome is logged via emit_metric("tts", ...) - a
# one-line, grep-able, never-silent record (`.metrics.log`), matching the
# rest of the repo's metrics convention (see lib/relay-common.sh).
set -u

_TTS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Only source relay-config.sh if cfg_get isn't ALREADY defined - re-sourcing
# it unconditionally would re-run its top-level `RELAY_CONFIG_JSON="{}"`
# reset and silently wipe out a relay.toml the caller (tg-send.sh) already
# loaded before sourcing this file. Same guard-before-source shape as every
# other lib/*.sh dependency in this repo, just order-sensitive here because
# relay-config.sh carries top-level state, not only function definitions.
if ! declare -f cfg_get >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    [[ -f "$_TTS_LIB_DIR/relay-config.sh" ]] && source "$_TTS_LIB_DIR/relay-config.sh"
fi
declare -f cfg_get >/dev/null 2>&1 || cfg_get() { printf '%s' "$2"; }  # lib missing -> default-only shim
# shellcheck disable=SC1091
[[ -f "$_TTS_LIB_DIR/relay-common.sh" ]] && source "$_TTS_LIB_DIR/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }  # lib missing -> no-op shim
# Prefer Python 3.14 for tts_plain_text.py
if [[ -f "$_TTS_LIB_DIR/python.sh" ]]; then
    # shellcheck disable=SC1091
    source "$_TTS_LIB_DIR/python.sh"
fi
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }

# _tts_plain_text <text>
#
# Convert a formatted message (the raw markdown tg-send.sh receives, or the
# HTML lib/format.sh emits - both are handled) into clean plain-text PROSE
# for the TTS engine, so the voice reads WORDS, never the formatting SYMBOLS
# (`#` headers, `*`/`_` emphasis, `` ` ``/```` ``` ```` code, `<b>`/`<pre>`
# tags, `&lt;` entities, `>` quotes, `[k/n]` page headers, list markers).
# Prints the stripped text to stdout. The SENT text message is NEVER touched
# - only this spoken copy is stripped (the caller passes the result to
# tts_send_voice; the text send uses the original $MSG). v0.5.2.
#
# Code and links are, by the maintainer's choice, referenced rather than
# read: a code span/block -> `[tts].voice_code_ref` (default "code, see the
# text message"); a link -> "<label>, `[tts].voice_link_ref`" (default
# "see the text message"), never the URL characters. `[tts].speak_code`
# (default false) is an opt-in escape hatch to read code verbatim instead.
#
# The heavy lifting lives in lib/tts_plain_text.py (stdlib-only, same
# python3-for-text convention as lib/toml_to_json.py) so the transforms are
# regex-clean and directly unit-testable (tests/test_tts_plain_text.py).
# Skip-graceful: if python3 or the script is unavailable, or it produces an
# empty result from non-empty input, this returns the ORIGINAL text - the
# voice still speaks (unstripped) rather than being dropped, exactly like
# the rest of lib/tts.sh's never-block-the-message contract.
_tts_plain_text() {
    local text="$1" speak_code code_ref link_ref collapse out args
    speak_code="$(cfg_get '.tts.speak_code' 'false')"
    code_ref="$(cfg_get '.tts.voice_code_ref' 'ref. the message for the code')"
    link_ref="$(cfg_get '.tts.voice_link_ref' 'ref. the message for the link')"
    collapse="$(cfg_get '.tts.collapse_adjacent_refs' 'true')"
    args=(--code-ref "$code_ref" --link-ref "$link_ref")
    case "$speak_code" in
        true | 1 | yes | on) args+=(--speak-code) ;;
    esac
    case "$collapse" in
        false | 0 | no | off) args+=(--no-collapse-refs) ;;
    esac
    if command -v "${RELAY_PYTHON:-python3}" >/dev/null 2>&1 && [[ -f "$_TTS_LIB_DIR/tts_plain_text.py" ]]; then
        out="$(printf '%s' "$text" | relay_python "$_TTS_LIB_DIR/tts_plain_text.py" "${args[@]}" 2>/dev/null)"
        # Never blank a message that HAD content (a strip that collapses to
        # nothing falls back to the raw text - a voice note of symbols beats
        # no voice note at all, and the text send is unaffected regardless).
        if [[ -n "$out" || -z "$text" ]]; then
            printf '%s' "$out"
            return 0
        fi
    fi
    printf '%s' "$text"
}

# _tts_truncate_words <text> <max_chars>
# Word-boundary truncate for spoken_mode=short. Empty max or 0 = no truncate.
_tts_truncate_words() {
    local text="$1" max="$2" cut
    if [[ ! "$max" =~ ^[0-9]+$ ]] || (( max <= 0 )) || (( ${#text} <= max )); then
        printf '%s' "$text"
        return 0
    fi
    cut="${text:0:max}"
    # Prefer last space so we don't split mid-word.
    if [[ "$cut" == *" "* ]]; then
        cut="${cut% *}"
    fi
    printf '%s' "$cut"
}

# _tts_chunk_text <outvar> <text> <max_chars>
#
# v0.5.3. Splits <text> (flowing spoken prose - no line structure assumed;
# this runs AFTER _tts_plain_text has already collapsed it to one line) into
# an array of chunks, each <= <max_chars>, written into the caller's array
# named <outvar> (nameref, same convention as lib/format.sh's `_qb`/`_ob` and
# lib/code_highlight.sh's `_img_body`).
#
# This is the fix for the v0.5.1/v0.5.2 defect where `[tts].hook_voice_max_chars`
# HARD-TRUNCATED the spoken text with a bash substring cut
# (`${TEXT:0:MAX}`) - anything past the cap was silently dropped, so a long
# hook ping's voice note read only its first ~1500 chars ("one part" of the
# message, exactly the maintainer-reported symptom). Chunking instead
# guarantees every character of <text> ends up in SOME chunk - the caller
# (tg-send.sh) sends each chunk as its own ordered voice note, so the WHOLE
# message is always read, never truncated into silence.
#
# <max_chars> <= 0 (or non-numeric) means "unbounded" - <text> comes back as
# exactly one chunk, byte-identical, regardless of length. This is the
# explicit opt-out for a single-clip-covers-everything read (set
# `hook_voice_max_chars = 0` for that). A positive <max_chars> instead
# bounds each individual clip's length (piper/espeak-ng on a huge string is
# slow and not a great listen in one breath either) while still covering
# the full text, split ONLY on whitespace so a chunk boundary never lands
# mid-word - a spoken word cut in half is worse than a slightly-short
# chunk. A single word longer than <max_chars> (an unbroken run with no
# spaces) is hard-split as a last-resort fallback, mirroring tg-send.sh's
# own PAGES single-line fallback.
#
# Pure/deterministic, no I/O. Empty <text> -> an empty array (0 chunks).
_tts_chunk_text() {
    local -n _tts_chunks_out="$1"
    local text="$2" max="$3"
    _tts_chunks_out=()

    [[ -z "$text" ]] && return 0

    if [[ ! "$max" =~ ^[0-9]+$ ]] || (( max <= 0 )) || (( ${#text} <= max )); then
        _tts_chunks_out=("$text")
        return 0
    fi

    local -a words
    read -r -a words <<< "$text"  # whitespace split only - no glob expansion

    local cur="" word cand rest
    for word in "${words[@]}"; do
        if [[ -n "$cur" ]]; then
            cand="${cur} ${word}"
        else
            cand="$word"
        fi

        if (( ${#cand} <= max )); then
            cur="$cand"
            continue
        fi

        # cand overflowed this chunk: flush what we already had.
        [[ -n "$cur" ]] && _tts_chunks_out+=("$cur")
        cur=""

        if (( ${#word} <= max )); then
            cur="$word"
        else
            # A single word longer than max_chars: hard-split it (fallback).
            rest="$word"
            while (( ${#rest} > max )); do
                _tts_chunks_out+=("${rest:0:max}")
                rest="${rest:max}"
            done
            cur="$rest"
        fi
    done
    [[ -n "$cur" ]] && _tts_chunks_out+=("$cur")
    (( ${#_tts_chunks_out[@]} == 0 )) && _tts_chunks_out=("$text")
}

# _tts_pitch_filter <sample_rate>
#
# Optional, OFF-BY-DEFAULT depth knob: reads [tts].pitch from relay.toml -
# a signed number of SEMITONES to shift pitch by (negative = deeper/lower,
# e.g. "-1.5"), unset/empty/"0"/non-numeric -> no filter at all (today's
# behavior, byte-identical output). When set, prints an ffmpeg `-af` filter
# string that shifts pitch via `asetrate` (tape-speed-style: changes pitch
# AND tempo together) immediately compensated by `atempo` (tempo-only, no
# further pitch change) so DURATION is preserved - only pitch moves. Uses
# awk for the float math (semitones -> a linear rate factor,
# factor = 2^(semitones/12)) since bash has no floating point; a bad/awk-less
# environment prints nothing (skip-graceful - the caller just omits -af).
# This is a small, best-effort timbre nudge, NOT a substitute for picking a
# naturally deep voice model - see SETUP.md's "Voice messages (TTS)" section.
_tts_pitch_filter() {
    local rate="$1" semitones factor
    [[ "$rate" =~ ^[0-9]+$ ]] || return 0
    semitones="$(cfg_get '.tts.pitch' '')"
    [[ -z "$semitones" || "$semitones" == "0" ]] && return 0
    # Validate: optional leading -, digits, optional decimal part.
    [[ "$semitones" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] || return 0
    command -v awk >/dev/null 2>&1 || return 0
    factor="$(awk -v s="$semitones" 'BEGIN { printf "%.6f", 2 ^ (s / 12) }' 2>/dev/null)"
    [[ -n "$factor" ]] || return 0
    printf 'asetrate=%s*%s,aresample=%s,atempo=1/%s' "$rate" "$factor" "$rate" "$factor"
}

# tts_select_engine
#
# Resolves relay.toml's [tts].engine ("auto" | "piper" | "espeak"; default
# "auto") to the ONE usable engine, or nothing. Prints "piper"/"espeak" to
# stdout and returns 0, or prints nothing and returns 1.
#
#   engine = "auto"   (default) -> prefer piper (needs the binary AND a
#                       configured, existing [tts].voice_model file);
#                       fall back to espeak-ng (needs only the binary).
#   engine = "piper"  -> only try piper; no fallback (an explicit choice
#                       is respected, not silently swapped for the other).
#   engine = "espeak" -> only try espeak-ng.
#
# Neither usable (including "piper configured but no/missing voice_model,
# and espeak-ng also absent") -> skip-graceful, handled by the caller.
tts_select_engine() {
    local configured voice_model try_piper=0 try_espeak=0
    configured="$(cfg_get '.tts.engine' 'auto')"
    voice_model="$(cfg_get '.tts.voice_model' '')"
    case "$configured" in
        piper) try_piper=1 ;;
        espeak) try_espeak=1 ;;
        *) try_piper=1; try_espeak=1 ;;  # "auto" or anything unrecognized
    esac

    if (( try_piper )) && command -v piper >/dev/null 2>&1 \
        && [[ -n "$voice_model" && -f "$voice_model" ]]; then
        printf 'piper'
        return 0
    fi
    if (( try_espeak )) && command -v espeak-ng >/dev/null 2>&1; then
        printf 'espeak'
        return 0
    fi
    return 1
}

# tts_synthesize <engine> <text> <out_wav>
#
# Runs the given engine ("piper"|"espeak", as returned by
# tts_select_engine) over <text>, writing a WAV to <out_wav>. Returns 0
# iff <out_wav> exists and is non-empty afterward - never trusts the
# engine's own exit code alone (a truncated/empty WAV is treated the same
# as a hard failure).
tts_synthesize() {
    local engine="$1" text="$2" out_wav="$3"
    case "$engine" in
        piper)
            local voice_model length_scale piper_args=()
            voice_model="$(cfg_get '.tts.voice_model' '')"
            # Optional cadence knob (piper's own --length-scale: lower =
            # faster, default 1.0 = unchanged). Unset/empty/non-numeric ->
            # omit the flag entirely, so piper uses its built-in default -
            # byte-identical to before this knob existed.
            length_scale="$(cfg_get '.tts.length_scale' '')"
            if [[ "$length_scale" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
                piper_args=(--length-scale "$length_scale")
            fi
            printf '%s' "$text" | piper --model "$voice_model" "${piper_args[@]}" --output_file "$out_wav" >/dev/null 2>&1
            ;;
        espeak)
            espeak-ng -w "$out_wav" "$text" >/dev/null 2>&1
            ;;
        *)
            return 1
            ;;
    esac
    [[ -s "$out_wav" ]]
}

# tts_send_voice <bot_token> <chat_id> <text>
#
# The end-to-end pipeline: select an engine -> synthesize WAV -> transcode
# to OGG/OPUS via ffmpeg (Telegram sendVoice's expected format) -> POST
# multipart to the bot API. Falls back to sending the raw WAV via
# sendAudio when ffmpeg is unavailable (still SOME voice, not nothing);
# skip-graceful (return 1, one emit_metric line, no partial state left
# behind) at every other failure point. Always cleans up its own temp
# files, on every exit path. Uses the caller's $BRIDGE_DIR (already set
# by tg-send.sh before sourcing this file) only indirectly, via
# emit_metric's own $BRIDGE_DIR/.metrics.log default - no bridge_dir
# argument needed here.
tts_send_voice() {
    local bot_token="$1" chat_id="$2" text="$3"
    local engine tmp_wav="" tmp_ogg="" send_file send_field send_method resp rc

    engine="$(tts_select_engine)" || {
        emit_metric "tts" "skip" "no local TTS engine available (install piper+voice_model or espeak-ng - see SETUP.md)"
        return 1
    }

    tmp_wav="$(mktemp "${TMPDIR:-/tmp}/relay-tts-XXXXXX.wav")"

    if ! tts_synthesize "$engine" "$text" "$tmp_wav"; then
        emit_metric "tts" "skip" "engine=$engine synthesis produced no audio"
        rm -f "$tmp_wav"
        return 1
    fi

    send_file="$tmp_wav"
    send_field="audio"
    send_method="sendAudio"

    if command -v ffmpeg >/dev/null 2>&1; then
        tmp_ogg="${tmp_wav%.wav}.ogg"
        local wav_rate pitch_af ffmpeg_af_args=()
        if command -v ffprobe >/dev/null 2>&1; then
            wav_rate="$(ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate \
                -of csv=p=0 "$tmp_wav" 2>/dev/null)"
        fi
        pitch_af="$(_tts_pitch_filter "${wav_rate:-}")"
        [[ -n "$pitch_af" ]] && ffmpeg_af_args=(-af "$pitch_af")
        if ffmpeg -y -loglevel error -i "$tmp_wav" "${ffmpeg_af_args[@]}" -c:a libopus -b:a 32k "$tmp_ogg" >/dev/null 2>&1 \
            && [[ -s "$tmp_ogg" ]]; then
            send_file="$tmp_ogg"
            send_field="voice"
            send_method="sendVoice"
        elif [[ -n "$pitch_af" ]] \
            && ffmpeg -y -loglevel error -i "$tmp_wav" -c:a libopus -b:a 32k "$tmp_ogg" >/dev/null 2>&1 \
            && [[ -s "$tmp_ogg" ]]; then
            # Pitch filter itself failed to apply cleanly (bad ffmpeg build, odd
            # sample rate, ...) - never let an optional depth knob break voice
            # notes; retry once with NO filter (today's plain transcode) before
            # giving up. Skip-graceful all the way down.
            emit_metric "tts" "skip" "engine=$engine pitch filter failed, sent unfiltered"
            send_file="$tmp_ogg"
            send_field="voice"
            send_method="sendVoice"
        else
            emit_metric "tts" "skip" "engine=$engine ffmpeg conversion to opus failed"
            rm -f "$tmp_wav" "$tmp_ogg"
            return 1
        fi
    fi
    # else: ffmpeg absent -> send_file/send_field/send_method stay the WAV/sendAudio fallback set above.

    resp="$(curl -s -m 30 -X POST "https://api.telegram.org/bot${bot_token}/${send_method}" \
        -F "chat_id=${chat_id}" \
        -F "${send_field}=@${send_file}" 2>/dev/null)"
    rc=$?

    rm -f "$tmp_wav" "$tmp_ogg"

    if [[ $rc -eq 0 && "$resp" == *'"ok":true'* ]]; then
        emit_metric "tts" "sent" "engine=$engine method=$send_method"
        return 0
    fi
    emit_metric "tts" "skip" "engine=$engine ${send_method} request failed"
    return 1
}

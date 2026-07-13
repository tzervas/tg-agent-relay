#!/bin/bash
# fetch-voices.sh - One-command piper voice model downloader.
#
# Downloads a recommended, natural-sounding en_US piper voice (both the
# `.onnx` model AND its `.onnx.json` config - piper needs both) into
# ./voices/, so `relay.toml`'s `[tts] voice_model = ".../voices/<voice>.onnx"`
# has something to point at. See SETUP.md's "Voice messages (TTS)" section
# for the full piper install + relay.toml wiring.
#
# Usage:
#   ./fetch-voices.sh                  # fetch the default voice (joe - deep male)
#   ./fetch-voices.sh amy              # fetch a specific recommended voice
#   ./fetch-voices.sh --list           # print the recommended-voice table
#   ./fetch-voices.sh --all            # fetch every recommended voice
#
# Skip-graceful: a voice already present (onnx + json both exist, and, when
# a pinned sha256 is known, verified) is left alone and reported as
# up-to-date - re-running this script is always safe and cheap. A download
# or checksum failure removes the partial file and exits non-zero with a
# clear message (never leaves a corrupt/truncated .onnx in place) - the
# relay's own TTS skip-graceful path (lib/tts.sh) then applies as normal
# (falls back to espeak-ng, or plain text if that's absent too).
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICES_DIR="$BRIDGE_DIR/voices"

# --- Recommended voice table --------------------------------------------
# name | huggingface path (under rhasspy/piper-voices/resolve/main/) | sha256 of the .onnx
# (sha256 pinned from this script's own verified download - see the
# fetch-voices.sh entry in CHANGELOG.md / this repo's TTS-upgrade PR. Empty
# means "not yet pinned here" - the download still succeeds, just unverified.)
_voice_row() {
    case "$1" in
        joe)
            printf '%s\t%s\t%s\n' \
                "en_US-joe-medium" \
                "en/en_US/joe/medium/en_US-joe-medium.onnx" \
                "58afce0321b8d9c46d7cdf9c16500cc55a793b4220212dba6b70fb788b3baf06"
            ;;
        hfc_male)
            printf '%s\t%s\t%s\n' \
                "en_US-hfc_male-medium" \
                "en/en_US/hfc_male/medium/en_US-hfc_male-medium.onnx" \
                "d11e403a02bdf5a670c877b3dc56e0e1c8cece6fb30289586314dffdc0a78cb0"
            ;;
        ryan)
            printf '%s\t%s\t%s\n' \
                "en_US-ryan-high" \
                "en/en_US/ryan/high/en_US-ryan-high.onnx" \
                "b3990d7606e183ec8dbfba70a4607074f162de1a0c412e0180d1ff60bb154eca"
            ;;
        lessac)
            printf '%s\t%s\t%s\n' \
                "en_US-lessac-medium" \
                "en/en_US/lessac/medium/en_US-lessac-medium.onnx" \
                "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f"
            ;;
        amy)
            printf '%s\t%s\t%s\n' \
                "en_US-amy-medium" \
                "en/en_US/amy/medium/en_US-amy-medium.onnx" \
                "b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18"
            ;;
        *)
            return 1
            ;;
    esac
}
ALL_VOICES=(joe hfc_male ryan lessac amy)
DEFAULT_VOICE="joe"

HF_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main"

_usage() {
    cat <<'EOF'
Usage: fetch-voices.sh [voice-name | --all | --list]

Recommended voices:
  joe        en_US-joe-medium        (default - deep, full male narrator, ~60MB)
  hfc_male   en_US-hfc_male-medium   (alternative deep male, brighter timbre, ~60MB)
  ryan       en_US-ryan-high         (alternative male, higher/lighter, high-quality, ~120MB)
  lessac     en_US-lessac-medium     (neutral/warm general-purpose narrator, ~60MB)
  amy        en_US-amy-medium        (female, bright/conversational, ~60MB)

Measured (autocorrelation pitch + FFT spectral-centroid estimates over a
sample sentence, from this script's own verified downloads): joe has the
lowest median pitch (~112Hz) AND the darkest/fullest timbre (spectral
centroid ~1.9kHz) of the three male candidates tried - the deepest-sounding
of the set. hfc_male has similar pitch but a brighter timbre; ryan-high is
both higher-pitched and brighter.

With no argument, fetches the default (joe) voice into ./voices/. Pair it
with relay.toml's optional [tts].pitch (a small negative semitone value,
e.g. "-1") for a further, subtle depth nudge - see lib/tts.sh's
_tts_pitch_filter header. See SETUP.md's "Voice messages (TTS)" section for
installing piper itself and wiring the result into relay.toml's [tts]
voice_model.
EOF
}

_list() {
    printf '%-10s %-24s %s\n' "NAME" "MODEL" "SIZE (approx)"
    local v row name size
    for v in "${ALL_VOICES[@]}"; do
        row="$(_voice_row "$v")"
        name="$(printf '%s' "$row" | cut -f1)"
        size="~60MB"
        [[ "$v" == "ryan" ]] && size="~120MB"
        printf '%-10s %-24s %s\n' "$v" "$name" "$size"
    done
}

# _fetch_one <voice-key>
# Downloads <voice-key>'s .onnx + .onnx.json into $VOICES_DIR, skipping if
# already present + checksum-clean, verifying sha256 when pinned above.
_fetch_one() {
    local key="$1" row name hf_path want_sha
    row="$(_voice_row "$key")" || {
        echo "fetch-voices.sh: unknown voice '$key' - see --list" >&2
        return 1
    }
    name="$(printf '%s' "$row" | cut -f1)"
    hf_path="$(printf '%s' "$row" | cut -f2)"
    want_sha="$(printf '%s' "$row" | cut -f3)"

    local onnx="$VOICES_DIR/${name}.onnx" json="$VOICES_DIR/${name}.onnx.json"
    mkdir -p "$VOICES_DIR"

    if [[ -s "$onnx" && -s "$json" ]]; then
        if [[ -n "$want_sha" ]] && command -v sha256sum >/dev/null 2>&1; then
            local got_sha
            got_sha="$(sha256sum "$onnx" | cut -d' ' -f1)"
            if [[ "$got_sha" == "$want_sha" ]]; then
                echo "fetch-voices.sh: $name already present + checksum-verified, skipping."
                return 0
            fi
            echo "fetch-voices.sh: $name present but checksum mismatch - re-downloading." >&2
        else
            echo "fetch-voices.sh: $name already present, skipping (no sha256sum to verify or no pin recorded)."
            return 0
        fi
    fi

    echo "fetch-voices.sh: fetching $name (~60MB) ..."
    if ! curl -fL --progress-bar -o "$onnx.part" "$HF_BASE/$hf_path"; then
        echo "fetch-voices.sh: download failed for $name.onnx" >&2
        rm -f "$onnx.part"
        return 1
    fi
    if ! curl -fL --progress-bar -o "$json.part" "$HF_BASE/${hf_path}.json"; then
        echo "fetch-voices.sh: download failed for $name.onnx.json" >&2
        rm -f "$onnx.part" "$json.part"
        return 1
    fi

    if [[ -n "$want_sha" ]] && command -v sha256sum >/dev/null 2>&1; then
        local got_sha
        got_sha="$(sha256sum "$onnx.part" | cut -d' ' -f1)"
        if [[ "$got_sha" != "$want_sha" ]]; then
            echo "fetch-voices.sh: checksum mismatch for $name.onnx (got $got_sha, want $want_sha) - not installing." >&2
            rm -f "$onnx.part" "$json.part"
            return 1
        fi
    fi

    mv "$onnx.part" "$onnx"
    mv "$json.part" "$json"
    echo "fetch-voices.sh: $name installed -> $onnx"
    echo "  Set in relay.toml: voice_model = \"$onnx\""
}

main() {
    case "${1:-}" in
        -h|--help) _usage; exit 0 ;;
        --list) _list; exit 0 ;;
        --all)
            local rc=0 v
            for v in "${ALL_VOICES[@]}"; do
                _fetch_one "$v" || rc=1
            done
            exit "$rc"
            ;;
        "") _fetch_one "$DEFAULT_VOICE" ;;
        *) _fetch_one "$1" ;;
    esac
}

main "$@"

#!/bin/bash
# relay-notify.sh - Generic, harness-agnostic status entry point.
#
# ANY agent or harness - not just Claude Code - can push a status update to
# Telegram through this ONE script, without knowing anything about
# Telegram's API or tg-send.sh's pagination/dedup details. This is the
# harness-neutral core of TG Agent Relay; per-harness adapters
# (adapters/claude-code.sh, ...) parse THEIR platform's native event
# shape and hand the result to this script (see adapters/README.md).
#
# Usage:
#   relay-notify.sh "<free-form status text>"          # raw passthrough (args)
#   echo "<free-form status text>" | relay-notify.sh     # raw passthrough (stdin)
#   relay-notify.sh --label "deploy" "finished OK"        # structured (args)
#   echo '{"label":"deploy","text":"finished OK"}' \
#     | relay-notify.sh                                     # structured (JSON stdin)
#   relay-notify.sh --raw "<already-formatted text>"       # bypass ALL
#     # formatting (JSON-sniff + [generic] prefix) - what an adapter that
#     # already built its own summary string should use.
#
# Structured mode renders "<prefix ><label>: <text>" (prefix comes from
# relay.toml [generic].prefix; empty/absent by default, so the plain
# "<label>: <text>" the roadmap describes is what you get out of the box).
# Raw/passthrough mode sends the text exactly as given (after the
# never-silent huge-message cap below) - this is what plain `tg-send.sh`
# has always done, so calling relay-notify.sh with a bare string is a
# drop-in replacement for calling tg-send.sh directly.
#
# Config (relay.toml, optional - see relay.toml.example):
#   [general] page_size / hook_max_pages   (else TG_PAGE_SIZE / TG_HOOK_MAX_PAGES / hardcoded defaults)
#   [generic] prefix                       (else no prefix - unchanged passthrough)
# No relay.toml -> every value below falls back to today's env-var/hardcoded
# defaults, so this script's plain-text mode is byte-for-byte what
# tg-send.sh alone already did.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/relay-config.sh"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/relay-common.sh"

load_relay_config "$BRIDGE_DIR/relay.toml"

# Same knobs hook-notify.sh has always exposed, now also relay.toml-backed.
# Env var (if set) wins over relay.toml, which wins over the hardcoded
# default - this is the standard fallback order used throughout the repo.
MAX_PAGES="${TG_HOOK_MAX_PAGES:-$(cfg_get '.general.hook_max_pages' 6)}"
[[ "$MAX_PAGES" =~ ^[0-9]+$ ]] || MAX_PAGES=6
PAGE_SIZE="${TG_PAGE_SIZE:-$(cfg_get '.general.page_size' 3500)}"
[[ "$PAGE_SIZE" =~ ^[0-9]+$ ]] || PAGE_SIZE=3500
MAX_CHARS=$(( PAGE_SIZE * MAX_PAGES ))

RAW_MODE=0
LABEL=""

# Parse leading flags (--raw, --label <value>) before the positional text.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --raw)
            RAW_MODE=1
            shift
            ;;
        --label)
            LABEL="${2:-}"
            shift 2
            ;;
        --)
            shift
            break
            ;;
        *)
            break
            ;;
    esac
done

if [[ $# -gt 0 ]]; then
    INPUT="$*"
else
    INPUT="$(cat)"
fi

[[ -z "$INPUT" && -z "$LABEL" ]] && exit 0

if (( RAW_MODE == 1 )); then
    MSG="$INPUT"
else
    TEXT="$INPUT"
    # JSON-object structured mode: {"label":..,"text":..} - only sniffed
    # when no --label flag already supplied one and INPUT looks like a
    # JSON object (starts with '{'); real free-text status lines never
    # start that way in practice, so this never misfires on a raw message.
    if [[ -z "$LABEL" && "$INPUT" == \{* ]]; then
        JLABEL="$(printf '%s' "$INPUT" | jq -r '.label // empty' 2>/dev/null || true)"
        JTEXT="$(printf '%s' "$INPUT" | jq -r '.text // empty' 2>/dev/null || true)"
        if [[ -n "$JLABEL" || -n "$JTEXT" ]]; then
            LABEL="$JLABEL"
            TEXT="$JTEXT"
        fi
    fi

    PREFIX="$(cfg_get '.generic.prefix' '')"
    if [[ -n "$LABEL" ]]; then
        if [[ -n "$PREFIX" ]]; then
            MSG="${PREFIX} ${LABEL}: ${TEXT}"
        else
            MSG="${LABEL}: ${TEXT}"
        fi
    else
        if [[ -n "$PREFIX" ]]; then
            MSG="${PREFIX} ${TEXT}"
        else
            MSG="$TEXT"
        fi
    fi
fi

MSG="$(cap_if_huge "$MSG" "$MAX_CHARS" "$PAGE_SIZE")"

[[ -z "$MSG" ]] && exit 0
emit_metric "relay-notify" "generic_send" "$([[ $RAW_MODE -eq 1 ]] && echo raw || echo structured)"
"$BRIDGE_DIR/tg-send.sh" "$MSG" >/dev/null 2>&1
exit 0

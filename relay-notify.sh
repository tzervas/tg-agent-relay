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
#   [generic] format                       (else the built-in "<prefix ><label>: <text>" /
#                                            "<prefix ><text>" shape below - unchanged)
# No relay.toml -> every value below falls back to today's env-var/hardcoded
# defaults, so this script's plain-text mode is byte-for-byte what
# tg-send.sh alone already did.
#
# TG_SEND_SOURCE passthrough: if the caller (typically an adapter) exported
# TG_SEND_SOURCE=hook to mark this as an automated/unattended event, it's a
# real environment variable and so is inherited automatically by this
# script's own call to tg-send.sh below - no extra flag or plumbing needed
# here. See tg-send.sh's header ("Hook audio") and adapters/README.md
# step 6 for what it changes (a voice read-through for long/paginated
# pings, relay.toml [tts].hook_voice).
#
# [generic].format (structured/non---raw mode only - see render_template's
# header in lib/relay-common.sh for the substitution rules, shared with
# adapters/claude-code.sh's per-event [claude_code.<Event>].format):
# available placeholders are {prefix}, {label}, {text}. With no `format`
# configured, the original hardcoded shape below is used exactly as
# before - this is purely additive.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BRIDGE_DIR
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/exec-env.sh" ]] && source "$BRIDGE_DIR/lib/exec-env.sh"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/relay-config.sh"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/relay-common.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }

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
# Optional multi-backend routing (Phase 2). Env vars also accepted so
# adapters can export RELAY_BACKEND / RELAY_PROJECT without new flags.
NOTIFY_BACKEND="${RELAY_BACKEND:-}"
NOTIFY_PROJECT="${RELAY_PROJECT:-}"
NOTIFY_CHAT_ID="${RELAY_CHAT_ID:-}"
NOTIFY_THREAD_ID="${RELAY_THREAD_ID:-}"

# Parse leading flags (--raw, --label <value>, routing flags) before text.
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
        --backend)
            NOTIFY_BACKEND="${2:-}"
            shift 2
            ;;
        --project)
            NOTIFY_PROJECT="${2:-}"
            shift 2
            ;;
        --chat-id)
            NOTIFY_CHAT_ID="${2:-}"
            shift 2
            ;;
        --thread-id)
            NOTIFY_THREAD_ID="${2:-}"
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
    FORMAT="$(cfg_get '.generic.format' '')"
    if [[ -n "$FORMAT" ]]; then
        MSG="$(render_template "$FORMAT" prefix "$PREFIX" label "$LABEL" text "$TEXT")"
    elif [[ -n "$LABEL" ]]; then
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

# Comms templates + agent stamp (hooks / PR / plan / stop — no model calls).
if [[ -f "$BRIDGE_DIR/lib/comms_format.sh" ]]; then
    # shellcheck disable=SC1091
    source "$BRIDGE_DIR/lib/comms_format.sh"
    if declare -f comms_enrich_message >/dev/null 2>&1; then
        MSG="$(comms_enrich_message "$MSG")"
    fi
fi

# Benign goal-mode tool failures: suppress hook spam (v0.9.0).
if [[ "${TG_SEND_SOURCE:-}" == "hook" ]] && command -v "${RELAY_PYTHON:-python3}" >/dev/null 2>&1; then
    _GOAL_FILTERED="$(printf '%s' "$MSG" | relay_python -c "
from tg_agent_relay.goal_events import apply_goal_noise_policy
import sys
t=sys.stdin.read()
r=apply_goal_noise_policy(t, hook_event='${RELAY_HOOK_EVENT:-}', is_hook=True)
print('' if r is None else r)
" 2>/dev/null)" || _GOAL_FILTERED=""
    [[ -z "$_GOAL_FILTERED" ]] && exit 0
    MSG="$_GOAL_FILTERED"
fi

# PLAN messages: attach approve/reject inline keyboard on first send page.
unset RELAY_REPLY_MARKUP_JSON
if command -v "${RELAY_PYTHON:-python3}" >/dev/null 2>&1; then
    _PLAN_MARKUP="$(printf '%s' "$MSG" | relay_python -c "
from tg_agent_relay.plan_approve import maybe_reply_markup_for_body
import os, sys
body=sys.stdin.read()
m=maybe_reply_markup_for_body(body, os.environ.get('BRIDGE_DIR', '.'))
print(m or '')
" 2>/dev/null)" || _PLAN_MARKUP=""
    if [[ -n "$_PLAN_MARKUP" ]]; then
        export RELAY_REPLY_MARKUP_JSON="$_PLAN_MARKUP"
    fi
fi

MSG="$(cap_if_huge "$MSG" "$MAX_CHARS" "$PAGE_SIZE")"

[[ -z "$MSG" ]] && exit 0

# Forum thread outbound resolve (RELAY_SESSION / RELAY_PLATFORM / …) — P13.
if [[ -z "$NOTIFY_CHAT_ID" || -z "$NOTIFY_THREAD_ID" ]] \
    && [[ -n "${RELAY_SESSION:-}" || -n "${RELAY_PLATFORM:-}" || -n "${RELAY_WORKSTREAM:-}" \
        || -n "${RELAY_AGENT_HANDLE:-}" ]] \
    && command -v "${RELAY_PYTHON:-python3}" >/dev/null 2>&1; then
    _THREAD_RES="$("${RELAY_PYTHON:-python3}" -m tg_agent_relay.threads resolve-outbound \
        --bridge-dir "$BRIDGE_DIR" 2>/dev/null)" || _THREAD_RES=""
    if [[ -n "$_THREAD_RES" && "${_THREAD_RES##*|}" != "none" ]]; then
        IFS='|' read -r _tcid _ttid _ttitle _tkind <<< "$_THREAD_RES"
        [[ -z "$NOTIFY_CHAT_ID" && -n "$_tcid" ]] && NOTIFY_CHAT_ID="$_tcid"
        [[ -z "$NOTIFY_THREAD_ID" && -n "$_ttid" ]] && NOTIFY_THREAD_ID="$_ttid"
        if [[ -n "$_ttitle" && "${RELAY_THREAD_STAMP:-1}" != "0" && "$MSG" != *"🧵"* ]]; then
            MSG="🧵 ${_ttitle}
${MSG}"
        fi
    fi
fi

# Multi-backend outbound tag + chat targeting — only when [backends]/[[chats]]
# routing is configured. Without it, behavior stays byte-identical (no tag).
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/routing.sh" ]] && source "$BRIDGE_DIR/lib/routing.sh"
if declare -f route_has_routing_config >/dev/null 2>&1 \
    && route_has_routing_config \
    && [[ -n "$NOTIFY_BACKEND" ]]; then
    if declare -f route_format_tag >/dev/null 2>&1; then
        ROUTE_TAG="$(route_format_tag "$NOTIFY_BACKEND" "$NOTIFY_PROJECT")"
        if [[ -n "$ROUTE_TAG" && "$MSG" != "$ROUTE_TAG"* ]]; then
            MSG="${ROUTE_TAG} ${MSG}"
        fi
    fi
    # Reverse-lookup destination chat when not explicitly provided.
    if [[ -z "$NOTIFY_CHAT_ID" ]] && declare -f route_lookup_chat >/dev/null 2>&1; then
        LOOKUP="$(route_lookup_chat "$NOTIFY_BACKEND" "$NOTIFY_PROJECT")"
        if [[ -n "$LOOKUP" ]]; then
            NOTIFY_CHAT_ID="${LOOKUP%%|*}"
            thr="${LOOKUP#*|}"
            [[ -n "$thr" && -z "$NOTIFY_THREAD_ID" ]] && NOTIFY_THREAD_ID="$thr"
        fi
    fi
fi

emit_metric "relay-notify" "generic_send" "$([[ $RAW_MODE -eq 1 ]] && echo raw || echo structured)${NOTIFY_BACKEND:+ backend=$NOTIFY_BACKEND}"

# Pass routing env through to tg-send (chat override + thread).
export RELAY_BACKEND="${NOTIFY_BACKEND}"
export RELAY_PROJECT="${NOTIFY_PROJECT}"
[[ -n "$NOTIFY_CHAT_ID" ]] && export RELAY_CHAT_ID="$NOTIFY_CHAT_ID"
[[ -n "$NOTIFY_THREAD_ID" ]] && export RELAY_THREAD_ID="$NOTIFY_THREAD_ID"

"$BRIDGE_DIR/tg-send.sh" "$MSG" >/dev/null 2>&1
exit 0

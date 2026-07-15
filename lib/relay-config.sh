#!/bin/bash
# lib/relay-config.sh - Optional relay.toml loader, shared by every script.
#
# Contract (backward-compat, non-negotiable): if relay.toml is absent, or
# python3/tomllib is unavailable, or the file fails to parse, every cfg_get
# call falls through to the DEFAULT the caller passed in - which every
# caller sets to the script's pre-existing hardcoded/env-var default. So a
# bridge with no relay.toml behaves byte-identically to before relay.toml
# existed. This file never causes a script to error or change behavior on
# its own - it can only ADD an override when a value is actually present.
set -u

_RELAY_CFG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer Python 3.14 (see lib/python.sh).
if [[ -f "$_RELAY_CFG_DIR/python.sh" ]]; then
    # shellcheck disable=SC1091
    source "$_RELAY_CFG_DIR/python.sh"
fi
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }

RELAY_CONFIG_JSON="{}"

# load_relay_config [path-to-relay.toml]
#
# Safe to call more than once (re-parses each call; relay.toml is tiny, so
# this is cheap and always reflects the file's current contents).
load_relay_config() {
    local toml_file="${1:-}"
    RELAY_CONFIG_JSON="{}"

    local lib_dir bridge_dir overlay
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    bridge_dir="$(cd "$lib_dir/.." && pwd)"

    if [[ -n "$toml_file" && -f "$toml_file" ]] && command -v "${RELAY_PYTHON:-python3}" >/dev/null 2>&1; then
        local parsed
        parsed="$(relay_python "$lib_dir/toml_to_json.py" "$toml_file" 2>/dev/null)" || parsed="{}"
        [[ -n "$parsed" ]] && RELAY_CONFIG_JSON="$parsed"
    fi

    # Merge project-chat overlay (.chats.d/bindings.json) — written by
    # /project bind. Overlay entries override static [[chats]] rows with the
    # same chat_id+thread_id; other static rows are kept.
    overlay="${RELAY_CHATS_OVERLAY:-$bridge_dir/.chats.d/bindings.json}"
    if [[ -f "$overlay" ]] && command -v jq >/dev/null 2>&1; then
        RELAY_CONFIG_JSON="$(printf '%s' "$RELAY_CONFIG_JSON" | jq -c --slurpfile ov "$overlay" '
          def key($c): "\($c.chat_id // "")|\($c.thread_id // "")";
          (.chats // []) as $base
          | (($ov[0].chats // $ov[0] // []) | if type=="array" then . else [] end) as $over
          | ($over | map(key(.)) | unique) as $okeys
          | ($base | map(select((key(.) as $k | ($okeys | index($k) | not))))
            ) as $kept
          | .chats = ($kept + $over)
        ' 2>/dev/null)" || true
        [[ -z "$RELAY_CONFIG_JSON" ]] && RELAY_CONFIG_JSON="{}"
    fi
    return 0
}

# cfg_get "<jq filter expression>" "<default>"
#
# Example: cfg_get '.general.page_size' 3500
# Returns the default verbatim whenever the path is absent/null, jq is
# missing, or the config JSON is empty ("{}") - i.e. always, until a
# relay.toml with that key is loaded.
#
# Deliberately NOT `(${filter}) // empty`: jq's `//` treats `false` (and
# `0`, `""`) as falsy, not just `null` - so `enabled = false` in relay.toml
# would silently fall back to the caller's default instead of actually
# disabling anything, exactly the config a "toggle it off" feature needs to
# honor. The explicit null-check below only falls through on a genuinely
# ABSENT key.
cfg_get() {
    local filter="$1" default="$2" val
    command -v jq >/dev/null 2>&1 || { printf '%s' "$default"; return; }
    val="$(printf '%s' "$RELAY_CONFIG_JSON" | jq -r "(${filter}) as \$v | if \$v == null then empty else \$v end" 2>/dev/null)"
    if [[ -n "$val" ]]; then
        printf '%s' "$val"
    else
        printf '%s' "$default"
    fi
}

# cfg_has_section "<dotted.section>" - true (rc 0) iff relay.toml defines
# that section at all (used by tg-poll.sh to decide whether ANY commands
# are configured, so a bridge with no relay.toml never tags a message -
# the backward-compat guarantee for the command parser).
cfg_has_section() {
    local path="$1" val
    command -v jq >/dev/null 2>&1 || return 1
    val="$(printf '%s' "$RELAY_CONFIG_JSON" | jq -c "(.${path}) // empty" 2>/dev/null)"
    [[ -n "$val" && "$val" != "null" && "$val" != "{}" ]]
}

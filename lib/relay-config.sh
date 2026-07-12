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

RELAY_CONFIG_JSON="{}"

# load_relay_config [path-to-relay.toml]
#
# Safe to call more than once (re-parses each call; relay.toml is tiny, so
# this is cheap and always reflects the file's current contents).
load_relay_config() {
    local toml_file="${1:-}"
    RELAY_CONFIG_JSON="{}"

    [[ -n "$toml_file" && -f "$toml_file" ]] || return 0
    command -v python3 >/dev/null 2>&1 || return 0

    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    local parsed
    parsed="$(python3 "$lib_dir/toml_to_json.py" "$toml_file" 2>/dev/null)" || parsed="{}"
    [[ -n "$parsed" ]] && RELAY_CONFIG_JSON="$parsed"
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

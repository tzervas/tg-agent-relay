#!/bin/bash
# install-hooks.sh - One-command Claude Code hook installer/sync.
#
# Reads which Claude Code hook events are ENABLED for THIS bridge
# (relay.toml's [claude_code.<Event>].enabled, or - when relay.toml does not
# say - that event's install-time default from lib/claude-code-events.sh)
# and reconciles the matching `hooks.<Event>` entries in
# ~/.claude/settings.json (or --settings <path>) so each points at THIS
# bridge's hook-notify.sh. Re-run any time relay.toml changes to sync
# settings.json to match: newly-enabled events get wired in, newly-disabled
# ones get their relay-owned entry removed. `--uninstall` is the same sync
# with the wanted set forced empty - it removes every hook entry this
# script ever added and touches nothing else.
#
# Idempotent + merge-not-clobber, by construction:
#   - Only the ONE relay-owned hook entry per event - identified by its
#     exact `command` value (this bridge's own hook-notify.sh path) - is
#     added, updated, or removed.
#   - Every other top-level settings.json key (permissions, theme,
#     enabledPlugins, ...) is passed through byte-for-byte.
#   - Every OTHER tool's hook entry for the same event (a different
#     `command`) is preserved untouched, even inside the same
#     `hooks.<Event>` array as ours.
#   - Running it twice in a row with no relay.toml change is a reported
#     no-op, never a duplicate entry.
#   - The whole computed result is JSON-validated (twice: before AND after
#     writing to disk) before settings.json is ever touched; a validation
#     failure aborts with nothing written - this script never leaves
#     settings.json malformed or half-written.
#
# Usage:
#   install-hooks.sh                    # sync ~/.claude/settings.json
#   install-hooks.sh --settings PATH    # use a different settings.json
#   install-hooks.sh --uninstall        # remove every relay-owned hook entry
#   install-hooks.sh --dry-run          # print the plan, change nothing
#   install-hooks.sh --help
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/relay-config.sh"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/claude-code-events.sh"

SETTINGS_PATH="${CLAUDE_SETTINGS_JSON:-$HOME/.claude/settings.json}"
UNINSTALL=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --settings)
            SETTINGS_PATH="${2:-}"
            shift 2
            ;;
        --uninstall)
            UNINSTALL=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h | --help)
            sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            printf 'install-hooks.sh: unknown argument: %s (see --help)\n' "$1" >&2
            exit 2
            ;;
    esac
done

if ! command -v jq >/dev/null 2>&1; then
    printf 'install-hooks.sh: jq is required and was not found on PATH.\n' >&2
    exit 1
fi
if [[ -z "$SETTINGS_PATH" ]]; then
    printf 'install-hooks.sh: --settings needs a path.\n' >&2
    exit 2
fi

HOOK_CMD="$BRIDGE_DIR/hook-notify.sh"

load_relay_config "$BRIDGE_DIR/relay.toml"

is_event_enabled() {
    cfg_get ".claude_code.\"$1\".enabled" "$(cc_event_default_enabled "$1")"
}

# The set of events to WIRE. Forced empty under --uninstall - the sync
# algorithm below then naturally strips every relay-owned entry and adds
# nothing, which IS uninstall; no separate code path needed.
WANT_EVENTS=()
if (( UNINSTALL == 0 )); then
    for ev in "${CLAUDE_CODE_EVENTS[@]}"; do
        [[ "$(is_event_enabled "$ev")" == "true" ]] && WANT_EVENTS+=("$ev")
    done
fi

# Load the current settings.json, or start from {} if it does not exist
# yet. A file that DOES exist but fails to parse is a hard stop - never
# guess at malformed JSON, never silently overwrite it.
CURRENT="{}"
if [[ -f "$SETTINGS_PATH" ]]; then
    JQ_ERR="$(jq -c '.' "$SETTINGS_PATH" 2>&1 1>/dev/null)"
    if [[ -n "$JQ_ERR" ]]; then
        printf 'install-hooks.sh: %s exists but is not valid JSON - refusing to touch it.\n' "$SETTINGS_PATH" >&2
        printf '  jq error: %s\n' "$JQ_ERR" >&2
        exit 1
    fi
    CURRENT="$(jq -c '.' "$SETTINGS_PATH")"
fi

json_array_from_lines() {
    # Empty input -> [] (not [""]); jq -R -s slurps raw stdin as one string.
    jq -R -s 'split("\n") | map(select(length > 0))'
}

WANT_JSON="$(printf '%s\n' "${WANT_EVENTS[@]}" | json_array_from_lines)"
ALL_EVENTS_JSON="$(printf '%s\n' "${CLAUDE_CODE_EVENTS[@]}" | json_array_from_lines)"

# Reconcile every DOCUMENTED event (not just the wanted ones, so a
# newly-disabled event's stale relay entry gets cleaned up too) against
# $want. For each event's hooks.<Event> array: strip out just OUR
# command from every matcher-block (never drop a whole block merely
# because it also happens to contain our command alongside someone
# else's - only our own line item goes), drop any block left with zero
# hooks, then append a fresh relay-owned block if the event is wanted.
NEW="$(printf '%s' "$CURRENT" | jq \
    --argjson want "$WANT_JSON" \
    --arg cmd "$HOOK_CMD" \
    --argjson all_events "$ALL_EVENTS_JSON" \
    '
    def without_ours($cmd; $arr):
        ( $arr
          | map(.hooks = [ .hooks[]? | select(.command != $cmd) ])
          | map(select((.hooks | length) > 0))
        );

    .hooks = (.hooks // {})
    | reduce $all_events[] as $ev (.;
        ($ev) as $e
        | (.hooks[$e] // []) as $existing
        | (without_ours($cmd; $existing)) as $others
        | if ($want | index($e)) then
            .hooks[$e] = ($others + [{"matcher": "*", "hooks": [{"type": "command", "command": $cmd, "timeout": 10}]}])
          else
            if ($others | length) > 0 then
                .hooks[$e] = $others
            else
                .hooks |= (if has($e) then del(.[$e]) else . end)
            end
          end
      )
    ')"

if [[ -z "$NEW" ]] || ! printf '%s' "$NEW" | jq -e . >/dev/null 2>&1; then
    printf 'install-hooks.sh: failed to compute a valid settings.json - aborting, nothing written.\n' >&2
    exit 1
fi

# --- Report the diff, never-silent ------------------------------------------
REPORT="$(jq -n \
    --argjson old "$CURRENT" \
    --argjson new "$NEW" \
    --arg cmd "$HOOK_CMD" \
    --argjson events "$ALL_EVENTS_JSON" '
    def has_ours($obj; $ev):
        ( ($obj.hooks[$ev] // [])
          | any(.[].hooks[]?; .command == $cmd) );
    {
        added:   [ $events[] | select( has_ours($new; .) and (has_ours($old; .) | not) ) ],
        removed: [ $events[] | select( has_ours($old; .) and (has_ours($new; .) | not) ) ]
    }
    ')"

ADDED_COUNT="$(printf '%s' "$REPORT" | jq '.added | length')"
REMOVED_COUNT="$(printf '%s' "$REPORT" | jq '.removed | length')"

if (( ADDED_COUNT == 0 && REMOVED_COUNT == 0 )); then
    if (( UNINSTALL == 1 )); then
        printf 'install-hooks.sh: no relay-owned hook entries found in %s - nothing to uninstall.\n' "$SETTINGS_PATH"
    else
        printf 'install-hooks.sh: %s already matches the desired hook set (%d event(s) enabled) - no changes.\n' \
            "$SETTINGS_PATH" "${#WANT_EVENTS[@]}"
    fi
    exit 0
fi

printf 'install-hooks.sh: plan for %s (command: %s)\n' "$SETTINGS_PATH" "$HOOK_CMD"
printf '%s' "$REPORT" | jq -r '.added[]   | "  + wire    " + .'
printf '%s' "$REPORT" | jq -r '.removed[] | "  - remove  " + .'

if (( DRY_RUN == 1 )); then
    printf 'install-hooks.sh: --dry-run, %s left unchanged.\n' "$SETTINGS_PATH"
    exit 0
fi

mkdir -p "$(dirname "$SETTINGS_PATH")"
TMP="$(mktemp "${SETTINGS_PATH}.XXXXXX")"
printf '%s' "$NEW" | jq '.' > "$TMP"
if ! jq -e . "$TMP" >/dev/null 2>&1; then
    printf 'install-hooks.sh: post-write validation failed - leaving %s untouched, discarding %s.\n' "$SETTINGS_PATH" "$TMP" >&2
    rm -f "$TMP"
    exit 1
fi
mv "$TMP" "$SETTINGS_PATH"
printf 'install-hooks.sh: wrote %s (+%d/-%d relay hook entr%s)\n' \
    "$SETTINGS_PATH" "$ADDED_COUNT" "$REMOVED_COUNT" "$([[ $((ADDED_COUNT + REMOVED_COUNT)) -eq 1 ]] && echo y || echo ies)"
exit 0

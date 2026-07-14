#!/bin/bash
# install-grok-hooks.sh - One-command Grok Build hook installer/sync.
#
# Event list is the providers/grok catalog (via lib/provider_catalog.py) so it
# cannot drift from adapters/grok.sh. enabled flags still come from
# relay.toml [grok.<Event>].enabled (default = provider catalog
# HookEvent.default_enabled; shell lib/grok-events.sh is only a fallback if
# the catalog is unavailable).
#
# Writes ONLY ~/.grok/hooks/tg-agent-relay.json (or --hooks-file). Never
# edits Claude settings, other files under ~/.grok/hooks/, or any path outside
# the chosen hooks file. That file is fully owned by this script: the whole
# document is the desired set of enabled events pointing at THIS bridge's
# hook-notify-grok.sh. Re-run any time relay.toml changes to resync.
# `--uninstall` removes the managed file and touches nothing else.
#
# Safety invariants:
#   - Running twice with no relay.toml / catalog change is a reported no-op;
#     the file is not rewritten when content already matches.
#   - A hooks file that exists but fails to parse is a hard stop - never
#     guess at malformed JSON, never overwrite it.
#   - The computed document is JSON-validated before write, and the staged
#     temp file is validated again before rename - this script never leaves
#     a half-written or invalid hooks file.
#   - Atomic write: temp in the same directory + mv.
#
# Usage:
#   install-grok-hooks.sh                    # sync default hooks file
#   install-grok-hooks.sh --hooks-file PATH  # use a different path
#   install-grok-hooks.sh --uninstall        # remove the managed hooks file
#   install-grok-hooks.sh --dry-run          # print the plan, change nothing
#   install-grok-hooks.sh --help
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$BRIDGE_DIR/lib/relay-config.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/grok-events.sh" ]] && source "$BRIDGE_DIR/lib/grok-events.sh"

HOOKS_FILE="${GROK_HOOKS_FILE:-$HOME/.grok/hooks/tg-agent-relay.json}"
UNINSTALL=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hooks-file)
            HOOKS_FILE="${2:-}"
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
            sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            printf 'install-grok-hooks.sh: unknown argument: %s (see --help)\n' "$1" >&2
            exit 2
            ;;
    esac
done

if ! command -v jq >/dev/null 2>&1; then
    printf 'install-grok-hooks.sh: jq is required and was not found on PATH.\n' >&2
    exit 1
fi
if [[ -z "$HOOKS_FILE" ]]; then
    printf 'install-grok-hooks.sh: --hooks-file needs a path.\n' >&2
    exit 2
fi

HOOK_CMD="$BRIDGE_DIR/hook-notify-grok.sh"

load_relay_config "$BRIDGE_DIR/relay.toml"

# All event names from provider catalog (providers/grok); shell array fallback.
mapfile -t ALL_EVENTS < <(relay_python "$BRIDGE_DIR/lib/provider_catalog.py" events grok --names-only 2>/dev/null)
if [[ ${#ALL_EVENTS[@]} -eq 0 ]]; then
    # Fallback to shell catalog when Python / providers package is unavailable.
    if declare -p GROK_EVENTS >/dev/null 2>&1; then
        ALL_EVENTS=("${GROK_EVENTS[@]}")
    fi
fi
if [[ ${#ALL_EVENTS[@]} -eq 0 ]]; then
    printf 'install-grok-hooks.sh: no Grok events from provider_catalog or grok-events.sh\n' >&2
    exit 1
fi

is_event_enabled() {
    local ev="$1" def
    # Provider.hook_events.default_enabled via catalog.
    def="$(relay_python -c "
import sys
sys.path.insert(0, '$BRIDGE_DIR')
from providers.base import get_provider
import providers
p=get_provider('grok')
print('true' if p and p.default_enabled('$ev') else 'false')
" 2>/dev/null)"
    [[ -z "$def" ]] && def="$(grok_event_default_enabled "$ev" 2>/dev/null || echo false)"
    cfg_get ".grok.\"$ev\".enabled" "$def"
}

# Wanted set is empty under --uninstall so the computed document has no
# event entries; uninstall then removes the managed file entirely.
WANT_EVENTS=()
if (( UNINSTALL == 0 )); then
    for ev in "${ALL_EVENTS[@]}"; do
        [[ -z "$ev" ]] && continue
        [[ "$(is_event_enabled "$ev")" == "true" ]] && WANT_EVENTS+=("$ev")
    done
fi

json_array_from_lines() {
    # Empty input -> [] (not [""]); jq -R -s slurps raw stdin as one string.
    jq -R -s 'split("\n") | map(select(length > 0))'
}

WANT_JSON="$(printf '%s\n' "${WANT_EVENTS[@]}" | json_array_from_lines)"

# Desired document: only this bridge's command, only enabled events.
# Full ownership of the managed file (unlike Claude settings merge).
NEW="$(jq -n \
    --arg cmd "$HOOK_CMD" \
    --argjson events "$WANT_JSON" \
    '{
      description: "TG Agent Relay — Grok Build provider (install-grok-hooks.sh)",
      hooks: (reduce $events[] as $ev ({}; .[$ev] = [{
        hooks: [{type: "command", command: $cmd, timeout: 10}]
      }]))
    }')"

if [[ -z "$NEW" ]] || ! printf '%s' "$NEW" | jq -e . >/dev/null 2>&1; then
    printf 'install-grok-hooks.sh: failed to compute a valid hooks document - aborting, nothing written.\n' >&2
    exit 1
fi

# Load the current hooks file, or treat a missing file as empty. A file that
# DOES exist but fails to parse is a hard stop - never overwrite bad JSON.
CURRENT=""
CURRENT_EXISTS=0
if [[ -f "$HOOKS_FILE" ]]; then
    CURRENT_EXISTS=1
    JQ_ERR="$(jq -c '.' "$HOOKS_FILE" 2>&1 1>/dev/null)"
    if [[ -n "$JQ_ERR" ]]; then
        printf 'install-grok-hooks.sh: %s exists but is not valid JSON - refusing to touch it.\n' "$HOOKS_FILE" >&2
        printf '  jq error: %s\n' "$JQ_ERR" >&2
        exit 1
    fi
    CURRENT="$(jq -c '.' "$HOOKS_FILE")"
fi

# --- Uninstall path: remove managed file only --------------------------------
if (( UNINSTALL == 1 )); then
    if (( CURRENT_EXISTS == 0 )); then
        printf 'install-grok-hooks.sh: no managed hooks file at %s - nothing to uninstall.\n' "$HOOKS_FILE"
        exit 0
    fi
    printf 'install-grok-hooks.sh: plan for %s\n' "$HOOKS_FILE"
    printf '  - remove managed hooks file\n'
    if (( DRY_RUN == 1 )); then
        printf 'install-grok-hooks.sh: --dry-run, %s left unchanged.\n' "$HOOKS_FILE"
        exit 0
    fi
    rm -f "$HOOKS_FILE"
    printf 'install-grok-hooks.sh: removed %s\n' "$HOOKS_FILE"
    exit 0
fi

# --- No-op when on-disk content already matches the desired document ---------
if (( CURRENT_EXISTS == 1 )); then
    if printf '%s' "$CURRENT" | jq -e --argjson new "$NEW" '. == $new' >/dev/null 2>&1; then
        printf 'install-grok-hooks.sh: %s already matches the desired hook set (%d event(s) enabled) - no changes.\n' \
            "$HOOKS_FILE" "${#WANT_EVENTS[@]}"
        exit 0
    fi
fi

# --- Report the plan, never-silent -------------------------------------------
printf 'install-grok-hooks.sh: plan for %s (command: %s)\n' "$HOOKS_FILE" "$HOOK_CMD"
if (( ${#WANT_EVENTS[@]} > 0 )); then
    printf '  events enabled: %s\n' "${WANT_EVENTS[*]}"
else
    printf '  events enabled: (none)\n'
fi
if (( CURRENT_EXISTS == 1 )); then
    printf '  action: rewrite managed hooks file\n'
else
    printf '  action: create managed hooks file\n'
fi

if (( DRY_RUN == 1 )); then
    printf 'install-grok-hooks.sh: --dry-run, %s left unchanged.\n' "$HOOKS_FILE"
    exit 0
fi

# Atomic write: stage next to the target so mv stays on the same filesystem,
# validate the staged file, then rename into place.
mkdir -p "$(dirname "$HOOKS_FILE")"
TMP="$(mktemp "${HOOKS_FILE}.XXXXXX")"
# shellcheck disable=SC2064
trap 'rm -f "$TMP"' EXIT
printf '%s' "$NEW" | jq '.' > "$TMP"
if ! jq -e . "$TMP" >/dev/null 2>&1; then
    printf 'install-grok-hooks.sh: post-write validation failed - leaving %s untouched, discarding staged file.\n' \
        "$HOOKS_FILE" >&2
    exit 1
fi
mv "$TMP" "$HOOKS_FILE"
trap - EXIT
printf 'install-grok-hooks.sh: wrote %s (%d event(s): %s)\n' \
    "$HOOKS_FILE" "${#WANT_EVENTS[@]}" "${WANT_EVENTS[*]:-(none)}"
exit 0

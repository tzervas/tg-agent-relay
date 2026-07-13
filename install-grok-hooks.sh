#!/bin/bash
# install-grok-hooks.sh - Sync Grok Build hooks from provider catalog + relay.toml.
#
# Event list is the providers/grok catalog (via lib/provider_catalog.py) so it
# cannot drift from adapters/grok.sh. enabled flags still come from
# relay.toml [grok.<Event>].enabled (default = provider catalog default).
#
# Writes ONLY: ~/.grok/hooks/tg-agent-relay.json (override --hooks-file)
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
        --hooks-file) HOOKS_FILE="${2:-}"; shift 2 ;;
        --uninstall) UNINSTALL=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h | --help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            printf 'install-grok-hooks.sh: unknown argument: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

if ! command -v jq >/dev/null 2>&1; then
    printf 'install-grok-hooks.sh: jq is required\n' >&2
    exit 1
fi
if ! command -v "${RELAY_PYTHON:-python3}" >/dev/null 2>&1; then
    printf 'install-grok-hooks.sh: python3 is required\n' >&2
    exit 1
fi

HOOK_CMD="$BRIDGE_DIR/hook-notify-grok.sh"
load_relay_config "$BRIDGE_DIR/relay.toml"

# All event names from provider catalog
mapfile -t ALL_EVENTS < <(relay_python "$BRIDGE_DIR/lib/provider_catalog.py" events grok --names-only 2>/dev/null)
if [[ ${#ALL_EVENTS[@]} -eq 0 ]]; then
    # Fallback to shell catalog
    ALL_EVENTS=("${GROK_EVENTS[@]}")
fi

is_event_enabled() {
    local ev="$1" def
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

WANT_EVENTS=()
if (( UNINSTALL == 0 )); then
    for ev in "${ALL_EVENTS[@]}"; do
        [[ -z "$ev" ]] && continue
        [[ "$(is_event_enabled "$ev")" == "true" ]] && WANT_EVENTS+=("$ev")
    done
fi

NEW="$(jq -n \
    --arg cmd "$HOOK_CMD" \
    --argjson events "$(printf '%s\n' "${WANT_EVENTS[@]}" | jq -R -s 'split("\n") | map(select(length > 0))')" \
    '{
      description: "TG Agent Relay — Grok Build provider (install-grok-hooks.sh)",
      hooks: (reduce $events[] as $ev ({}; .[$ev] = [{
        hooks: [{type:"command", command:$cmd, timeout:10}]
      }]))
    }')"

if [[ -z "$NEW" ]] || ! printf '%s' "$NEW" | jq -e . >/dev/null 2>&1; then
    printf 'install-grok-hooks.sh: failed to build hooks document\n' >&2
    exit 1
fi

CURRENT="{}"
if [[ -f "$HOOKS_FILE" ]]; then
    if ! jq -e . "$HOOKS_FILE" >/dev/null 2>&1; then
        printf 'install-grok-hooks.sh: %s is not valid JSON — refusing\n' "$HOOKS_FILE" >&2
        exit 1
    fi
    CURRENT="$(jq -c '.' "$HOOKS_FILE")"
fi

if printf '%s' "$CURRENT" | jq -e --argjson new "$NEW" '. == $new' >/dev/null 2>&1; then
    if (( UNINSTALL == 1 )) && [[ ! -f "$HOOKS_FILE" ]]; then
        printf 'install-grok-hooks.sh: nothing to uninstall at %s\n' "$HOOKS_FILE"
        exit 0
    fi
    if (( UNINSTALL == 0 )); then
        printf 'install-grok-hooks.sh: %s already matches (%d event(s)) — no changes.\n' \
            "$HOOKS_FILE" "${#WANT_EVENTS[@]}"
        exit 0
    fi
fi

printf 'install-grok-hooks.sh: plan for %s\n' "$HOOKS_FILE"
printf '  events enabled: %s\n' "${WANT_EVENTS[*]:-(none)}"
if (( DRY_RUN == 1 )); then
    printf 'install-grok-hooks.sh: --dry-run, unchanged.\n'
    exit 0
fi

if (( UNINSTALL == 1 )); then
    rm -f "$HOOKS_FILE"
    printf 'install-grok-hooks.sh: removed %s\n' "$HOOKS_FILE"
    exit 0
fi

mkdir -p "$(dirname "$HOOKS_FILE")"
TMP="$(mktemp "${HOOKS_FILE}.XXXXXX")"
printf '%s' "$NEW" | jq '.' > "$TMP"
mv "$TMP" "$HOOKS_FILE"
printf 'install-grok-hooks.sh: wrote %s (%d event(s): %s)\n' \
    "$HOOKS_FILE" "${#WANT_EVENTS[@]}" "${WANT_EVENTS[*]}"
exit 0

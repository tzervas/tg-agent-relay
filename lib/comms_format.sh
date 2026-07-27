#!/bin/bash
# lib/comms_format.sh - Outbound comms enrichment (classify + stamp).
set -u

comms_enrich_message() {
    local body="${1:-}"
    local hook_event="${2:-${RELAY_HOOK_EVENT:-}}"
    local bridge_dir="${BRIDGE_DIR:-}"
    [[ -z "$body" ]] && { printf '%s' "$body"; return 0; }

    if [[ -n "$bridge_dir" && -f "$bridge_dir/lib/python.sh" ]]; then
        # shellcheck disable=SC1091
        source "$bridge_dir/lib/python.sh"
        declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
        if relay_python -c "import tg_agent_relay.comms_format" >/dev/null 2>&1; then
            local cwd="${RELAY_CWD:-$(pwd)}"
            local out
            out="$(printf '%s' "$body" | relay_python -m tg_agent_relay.comms_format \
                --hook-event "$hook_event" --cwd "$cwd" 2>/dev/null)" || out=""
            if [[ -n "$out" ]]; then
                printf '%s' "$out"
                return 0
            fi
        fi
    fi
    printf '%s' "$body"
}

#!/bin/bash
# lib/agent_handle.sh - @repo-branch handle construction (no model calls).
set -u

agent_handle_build() {
    local repo="${1:-${RELAY_REPO:-}}"
    local branch="${2:-${RELAY_BRANCH:-}}"
    local bridge_dir="${BRIDGE_DIR:-}"
    if [[ -n "$bridge_dir" && -f "$bridge_dir/lib/python.sh" ]]; then
        source "$bridge_dir/lib/python.sh"
        declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
        if relay_python -c "import tg_agent_relay.agent_handle" >/dev/null 2>&1; then
            relay_python -m tg_agent_relay.agent_handle --repo "$repo" --branch "$branch" 2>/dev/null || true
            return 0
        fi
    fi
    _agent_handle_build_shell "$repo" "$branch"
}

agent_handle_from_env() {
    local bridge_dir="${BRIDGE_DIR:-}"
    if [[ -n "$bridge_dir" && -f "$bridge_dir/lib/python.sh" ]]; then
        source "$bridge_dir/lib/python.sh"
        declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
        if relay_python -c "import tg_agent_relay.agent_handle" >/dev/null 2>&1; then
            relay_python -c "from tg_agent_relay.agent_handle import build_handle_from_env; print(build_handle_from_env())" 2>/dev/null || true
            return 0
        fi
    fi
    _agent_handle_build_shell "${RELAY_REPO:-}" "${RELAY_BRANCH:-}"
}

_agent_handle_build_shell() {
    local repo="${1:-}"
    local branch="${2:-}"
    local rname="${repo##*/}"
    rname="$(printf '%s' "$rname" | tr '[:upper:]' '[:lower:]')"
    rname="$(printf '%s' "$rname" | tr -cd '[:alnum:]')"
    rname="${rname:0:16}"
    branch="${branch#feat/}"
    branch="${branch#fix/}"
    branch="${branch#chore/}"
    branch="${branch#docs/}"
    branch="$(printf '%s' "$branch" | tr '[:upper:]' '[:lower:]')"
    branch="$(printf '%s' "$branch" | sed -E 's/[^a-z0-9]+/-/g; s/-+/-/g; s/^-|-$//g')"
    branch="${branch:0:20}"
    if [[ -z "$rname" && -z "$branch" ]]; then printf ''; return 0; fi
    if [[ -z "$rname" ]]; then printf '@%s' "$branch"
    elif [[ -z "$branch" ]]; then printf '@%s' "$rname"
    else printf '@%s-%s' "$rname" "$branch"; fi
}

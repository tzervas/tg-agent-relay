#!/bin/bash
# lib/agent_stamp.sh - Agent/repo stamp from env + git (no model calls).
set -u

agent_stamp_build() {
    local cwd="${1:-${RELAY_CWD:-$(pwd)}}"
    local bridge_dir="${BRIDGE_DIR:-}"
    if [[ -n "$bridge_dir" && -f "$bridge_dir/lib/python.sh" ]]; then
        # shellcheck disable=SC1091
        source "$bridge_dir/lib/python.sh"
        declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
        if relay_python -c "import tg_agent_relay.agent_stamp" >/dev/null 2>&1; then
            relay_python -m tg_agent_relay.agent_stamp --cwd "$cwd" 2>/dev/null || true
            return 0
        fi
    fi
    _agent_stamp_build_shell "$cwd"
}

_agent_stamp_build_shell() {
    local cwd="$1"
    local repo="${RELAY_REPO:-}"
    local branch="${RELAY_BRANCH:-}"
    local pr_url="${RELAY_PR_URL:-}"
    local pr_state="${RELAY_PR_STATE:-}"
    local pr_num="${RELAY_PR_NUMBER:-}"

    if [[ -z "$repo" || -z "$branch" ]] && command -v git >/dev/null 2>&1 && [[ -d "$cwd" ]]; then
        local remote br owner rname
        remote="$(git -C "$cwd" remote get-url origin 2>/dev/null || true)"
        br="$(git -C "$cwd" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
        [[ "$br" == "HEAD" ]] && br=""
        [[ -z "$branch" ]] && branch="$br"
        if [[ -z "$repo" && -n "$remote" ]]; then
            owner=""
            rname=""
            if [[ "$remote" =~ ^git@github\.com:([^/]+)/([^/.]+) ]]; then
                owner="${BASH_REMATCH[1]}"
                rname="${BASH_REMATCH[2]}"
            elif [[ "$remote" =~ ^https?://github\.com/([^/]+)/([^/.]+) ]]; then
                owner="${BASH_REMATCH[1]}"
                rname="${BASH_REMATCH[2]}"
            fi
            rname="${rname%.git}"
            [[ -n "$owner" && -n "$rname" ]] && repo="${owner}/${rname}"
        fi
    fi

    local branch_url=""
    if [[ -n "$repo" && "$repo" == */* && -n "$branch" ]]; then
        branch_url="https://github.com/${repo}/tree/${branch}"
    fi

    if [[ -z "$pr_url" && -n "$pr_num" && -n "$repo" && "$repo" == */* ]]; then
        pr_url="https://github.com/${repo}/pull/${pr_num}"
    fi

    if [[ -z "$pr_url" && "${RELAY_PR_LOOKUP:-0}" =~ ^(1|true|yes|on)$ ]] \
        && command -v gh >/dev/null 2>&1 && [[ -d "$cwd" ]]; then
        local gh_json gh_u gh_s
        gh_json="$(gh pr view --json url,state 2>/dev/null)" || gh_json=""
        if [[ -n "$gh_json" ]]; then
            gh_u="$(printf '%s' "$gh_json" | jq -r '.url // empty' 2>/dev/null)"
            gh_s="$(printf '%s' "$gh_json" | jq -r '.state // empty' 2>/dev/null | tr '[:upper:]' '[:lower:]')"
            [[ -n "$gh_u" ]] && pr_url="$gh_u"
            [[ -z "$pr_state" && -n "$gh_s" ]] && pr_state="$gh_s"
        fi
    fi

    [[ -n "$repo" || -n "$branch" ]] && printf '🏷 repo=%s branch=%s\n' "$repo" "$branch"
    [[ -n "$branch_url" ]] && printf '🔗 branch: %s\n' "$branch_url"
    [[ -n "$pr_url" ]] && printf '🔗 pr: %s\n' "$pr_url"
    if [[ -n "$pr_url" && -n "$pr_state" ]]; then
        printf '📌 status=%s\n' "$pr_state"
    fi
}

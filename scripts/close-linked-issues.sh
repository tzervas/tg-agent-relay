#!/bin/bash
# scripts/close-linked-issues.sh — Close issues linked by Fixes/Closes/Resolves.
#
# GitHub only auto-closes issues when a PR merges into the *default* branch
# (main). Our swarm merges into an integration branch first, so closing
# keywords never fire. This script uses the gh CLI / API to apply the same
# intent for any merged PR (or a PR about to merge).
#
# Usage:
#   bash scripts/close-linked-issues.sh --pr 74
#   bash scripts/close-linked-issues.sh --pr 74 --dry-run
#   bash scripts/close-linked-issues.sh --merged-into fix/tts-voice-full-message-v0.5.3 --limit 20
#   bash scripts/close-linked-issues.sh --merged-since 2026-07-14T00:00:00Z --limit 30
#
# Extracted keywords (same family as GitHub): fix|fixes|fixed|close|closes|
# closed|resolve|resolves|resolved, then #N (comma/"and" lists OK).
# Also picks up conventional titles like feat(#62): … when the number is in
# parentheses after a conventional type (secondary signal).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DRY_RUN=0
PR_NUM=""
MERGED_INTO=""
MERGED_SINCE=""
LIMIT=30
COMMENT=1

usage() {
    sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pr) PR_NUM="${2:-}"; shift 2 ;;
        --merged-into) MERGED_INTO="${2:-}"; shift 2 ;;
        --merged-since) MERGED_SINCE="${2:-}"; shift 2 ;;
        --limit) LIMIT="${2:-30}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --no-comment) COMMENT=0; shift ;;
        -h | --help) usage; exit 0 ;;
        *)
            printf 'close-linked-issues.sh: unknown arg: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

if ! command -v gh >/dev/null 2>&1; then
    printf 'close-linked-issues.sh: gh CLI required\n' >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    printf 'close-linked-issues.sh: python3 required for keyword parse\n' >&2
    exit 1
fi

# extract_issue_numbers — reads text on stdin, prints one issue number per line.
# (Must use python -c so a heredoc does not steal stdin from the pipe.)
extract_issue_numbers() {
    python3 -c '
import re, sys
text = sys.stdin.read()
kw = r"(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)"
found = set()
for m in re.finditer(rf"(?is)\b{kw}\b(?:\s*:)?\s*((?:#?\d+(?:\s*[,&]?\s*(?:and\s+)?#?\d+)*))", text):
    for n in re.findall(r"\d+", m.group(1)):
        found.add(int(n))
# Conventional titles: feat(#62): / test(#63):
for m in re.finditer(r"(?i)\b(?:feat|fix|docs|test|chore|refactor|perf|ci|build|style)\(#(\d+)\)", text):
    found.add(int(m.group(1)))
for n in sorted(found):
    print(n)
'
}

# close_one <issue> <pr> <base> <url>
close_one() {
    local issue="$1" pr="$2" base="$3" url="$4" state
    state="$(gh issue view "$issue" --json state --jq .state 2>/dev/null || echo MISSING)"
    if [[ "$state" == "MISSING" || -z "$state" ]]; then
        printf '  skip  #%s (not found)\n' "$issue"
        return 0
    fi
    if [[ "$state" == "CLOSED" ]]; then
        printf '  skip  #%s (already closed)\n' "$issue"
        return 0
    fi
    if (( DRY_RUN == 1 )); then
        printf '  dry   would close #%s (from PR #%s → %s)\n' "$issue" "$pr" "$base"
        return 0
    fi
    if (( COMMENT == 1 )); then
        gh issue close "$issue" --comment "$(cat <<EOF
Closed via merged PR #${pr} (\`${base}\`).

GitHub only auto-closes issues when the PR base is the default branch; this repo integrates on feature/integration branches first, so \`scripts/close-linked-issues.sh\` applies the same Fixes/Closes intent.

${url}
EOF
)" >/dev/null
    else
        gh issue close "$issue" >/dev/null
    fi
    printf '  closed #%s (PR #%s)\n' "$issue" "$pr"
}

process_pr() {
    local pr="$1" json title body base state merged url text nums
    json="$(gh pr view "$pr" --json number,title,body,baseRefName,state,mergedAt,url 2>/dev/null)" || {
        printf 'close-linked-issues.sh: cannot view PR #%s\n' "$pr" >&2
        return 1
    }
    title="$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("title") or "")')"
    body="$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("body") or "")')"
    base="$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("baseRefName") or "")')"
    state="$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state") or "")')"
    merged="$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("mergedAt") or "")')"
    url="$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("url") or "")')"

    text="${title}"$'\n'"${body}"
    # Commit subjects help when squash-merge titles drop body keywords.
    local commits
    commits="$(gh pr view "$pr" --json commits --jq '.commits[].messageHeadline' 2>/dev/null || true)"
    text="${text}"$'\n'"${commits}"
    nums="$(printf '%s' "$text" | extract_issue_numbers)"

    is_merged=0
    if [[ "$state" == "MERGED" || -n "$merged" ]]; then
        is_merged=1
    fi

    if (( is_merged == 0 )); then
        if [[ -z "$nums" ]]; then
            printf 'PR #%s (%s → %s): not merged; no Fixes/Closes refs\n' "$pr" "$state" "$base"
            return 0
        fi
        printf 'PR #%s (%s → %s): not merged — linked issues not closed yet:\n' "$pr" "$state" "$base"
        while IFS= read -r n; do
            [[ -z "$n" ]] && continue
            printf '  pending #%s\n' "$n"
        done <<<"$nums"
        return 0
    fi

    if [[ -z "$nums" ]]; then
        printf 'PR #%s: no linked issues found\n' "$pr"
        return 0
    fi
    printf 'PR #%s → %s\n' "$pr" "$base"
    while IFS= read -r n; do
        [[ -z "$n" ]] && continue
        close_one "$n" "$pr" "$base" "$url"
    done <<<"$nums"
}

if [[ -n "$PR_NUM" ]]; then
    process_pr "$PR_NUM"
    exit 0
fi

if [[ -z "$MERGED_INTO" && -z "$MERGED_SINCE" ]]; then
    printf 'close-linked-issues.sh: pass --pr N, or --merged-into BRANCH, or --merged-since ISO\n' >&2
    exit 2
fi

# List recently merged PRs via search API
# Prefer base branch filter when provided
QUERY="is:pr is:merged"
if [[ -n "$MERGED_INTO" ]]; then
    QUERY+=" base:${MERGED_INTO}"
fi
if [[ -n "$MERGED_SINCE" ]]; then
    # merged:>= requires date; accept ISO and trim to date if needed
    since_date="${MERGED_SINCE:0:10}"
    QUERY+=" merged:>=${since_date}"
fi

printf 'Searching: %s (limit %s)\n' "$QUERY" "$LIMIT"
mapfile -t PRS < <(gh pr list --state merged --search "$QUERY" --limit "$LIMIT" --json number --jq '.[].number' 2>/dev/null)
if [[ ${#PRS[@]} -eq 0 ]]; then
    # Fallback without search (repo default)
    if [[ -n "$MERGED_INTO" ]]; then
        mapfile -t PRS < <(gh pr list --state merged --base "$MERGED_INTO" --limit "$LIMIT" --json number --jq '.[].number')
    else
        mapfile -t PRS < <(gh pr list --state merged --limit "$LIMIT" --json number --jq '.[].number')
    fi
fi

if [[ ${#PRS[@]} -eq 0 ]]; then
    printf 'No merged PRs matched.\n'
    exit 0
fi

for pr in "${PRS[@]}"; do
    process_pr "$pr" || true
done

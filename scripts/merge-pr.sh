#!/bin/bash
# scripts/merge-pr.sh — Merge a PR into its base, then close linked issues.
#
# GitHub auto-close only runs for the default branch. After a successful merge
# into an integration branch, this invokes scripts/close-linked-issues.sh.
#
# Usage:
#   bash scripts/merge-pr.sh 74
#   bash scripts/merge-pr.sh 74 --squash
#   bash scripts/merge-pr.sh 74 --admin          # if branch protection requires
#   bash scripts/merge-pr.sh 74 --dry-run
#   bash scripts/merge-pr.sh 74 --no-delete      # keep head branch
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PR=""
MERGE_ARGS=()
DRY_RUN=0
DELETE=1
SKIP_CLOSE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --squash) MERGE_ARGS+=(--squash); shift ;;
        --merge) MERGE_ARGS+=(--merge); shift ;;
        --rebase) MERGE_ARGS+=(--rebase); shift ;;
        --admin) MERGE_ARGS+=(--admin); shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --no-delete) DELETE=0; shift ;;
        --skip-close) SKIP_CLOSE=1; shift ;;
        -h | --help)
            sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            if [[ -z "$PR" && "$1" =~ ^[0-9]+$ ]]; then
                PR="$1"
                shift
            else
                printf 'merge-pr.sh: unknown arg: %s\n' "$1" >&2
                exit 2
            fi
            ;;
    esac
done

if [[ -z "$PR" ]]; then
    printf 'usage: merge-pr.sh <pr-number> [--merge|--squash|--rebase] [--dry-run]\n' >&2
    exit 2
fi

# Default merge strategy: merge commit (preserves history; matches recent waves)
if [[ ${#MERGE_ARGS[@]} -eq 0 ]]; then
    MERGE_ARGS=(--merge)
fi

if (( DELETE == 1 )); then
    MERGE_ARGS+=(--delete-branch)
fi

printf 'merge-pr.sh: PR #%s %s\n' "$PR" "$(gh pr view "$PR" --json title --jq .title)"
if (( DRY_RUN == 1 )); then
    printf 'merge-pr.sh: --dry-run — would: gh pr merge %s %s\n' "$PR" "${MERGE_ARGS[*]}"
    bash "$REPO_ROOT/scripts/close-linked-issues.sh" --pr "$PR" --dry-run || true
    exit 0
fi

gh pr merge "$PR" "${MERGE_ARGS[@]}"

if (( SKIP_CLOSE == 0 )); then
    bash "$REPO_ROOT/scripts/close-linked-issues.sh" --pr "$PR"
fi

printf 'merge-pr.sh: done (PR #%s)\n' "$PR"

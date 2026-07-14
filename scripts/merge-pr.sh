#!/bin/bash
# scripts/merge-pr.sh — Merge a PR into its configured base.
#
# Branch model:
#   feat/* → dev   (integration; issues stay open)
#   dev    → main  (promote/release only via PR; issues/epics may close)
#
# Issue close runs only when base is main (GitHub native + close-linked-issues).
#
# Usage:
#   bash scripts/merge-pr.sh 74
#   bash scripts/merge-pr.sh 74 --squash
#   bash scripts/merge-pr.sh 74 --admin
#   bash scripts/merge-pr.sh 74 --dry-run
#   bash scripts/merge-pr.sh 74 --no-delete
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PR=""
MERGE_ARGS=()
DRY_RUN=0
DELETE=1
SKIP_CLOSE=0
MAIN_BRANCH="${RELAY_MAIN_BRANCH:-main}"

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
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
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

if [[ ${#MERGE_ARGS[@]} -eq 0 ]]; then
    MERGE_ARGS=(--merge)
fi

if (( DELETE == 1 )); then
    MERGE_ARGS+=(--delete-branch)
fi

meta="$(gh pr view "$PR" --json baseRefName,title --jq '{base:.baseRefName,title:.title}')"
base="$(printf '%s' "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)["base"])')"
title="$(printf '%s' "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)["title"])')"
printf 'merge-pr.sh: PR #%s → base=%s — %s\n' "$PR" "$base" "$title"

if (( DRY_RUN == 1 )); then
    printf 'merge-pr.sh: --dry-run — would: gh pr merge %s %s\n' "$PR" "${MERGE_ARGS[*]}"
    if [[ "$base" == "$MAIN_BRANCH" ]]; then
        bash "$REPO_ROOT/scripts/close-linked-issues.sh" --pr "$PR" --dry-run || true
    else
        printf 'merge-pr.sh: base=%s — issues would stay open (close only on %s)\n' "$base" "$MAIN_BRANCH"
    fi
    exit 0
fi

gh pr merge "$PR" "${MERGE_ARGS[@]}"

if (( SKIP_CLOSE == 0 )) && [[ "$base" == "$MAIN_BRANCH" ]]; then
    bash "$REPO_ROOT/scripts/close-linked-issues.sh" --pr "$PR"
elif [[ "$base" != "$MAIN_BRANCH" ]]; then
    printf 'merge-pr.sh: merged into %s — issues left open until promote to %s\n' "$base" "$MAIN_BRANCH"
fi

printf 'merge-pr.sh: done (PR #%s)\n' "$PR"

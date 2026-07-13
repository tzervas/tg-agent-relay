#!/bin/bash
# scripts/release.sh - Tag and publish a GitHub Release.
#
# Usage:
#   bash scripts/release.sh v0.6.0
#   bash scripts/release.sh v0.6.0 --dry-run
#   bash scripts/release.sh v0.6.0 --skip-tests
#   bash scripts/release.sh v0.6.0 --notes-file RELEASE_NOTES.md
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TAG="${1:-}"
shift || true
DRY_RUN=0
SKIP_TESTS=0
NOTES_FILE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --skip-tests) SKIP_TESTS=1; shift ;;
        --notes-file) NOTES_FILE="${2:-}"; shift 2 ;;
        -h|--help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            printf 'release.sh: unknown arg: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

if [[ -z "$TAG" || ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]]; then
    printf 'usage: release.sh vMAJOR.MINOR.PATCH [--dry-run] [--skip-tests] [--notes-file FILE]\n' >&2
    exit 2
fi

VER="${TAG#v}"
FILE_VER="$(tr -d '[:space:]' < VERSION 2>/dev/null || true)"
# Allow VERSION with or without -dev when cutting (must match base)
FILE_BASE="${FILE_VER%-dev}"
if [[ -n "$FILE_VER" && "$FILE_BASE" != "$VER" && "$FILE_VER" != "$VER" ]]; then
    printf 'release.sh: VERSION file is %s but tag is %s — update VERSION first.\n' "$FILE_VER" "$VER" >&2
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    printf 'release.sh: working tree not clean. Commit or stash first.\n' >&2
    git status -sb
    exit 1
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
    printf 'release.sh: tag %s already exists locally.\n' "$TAG" >&2
    exit 1
fi

if (( SKIP_TESTS == 0 )); then
    printf 'release.sh: running offline tests…\n'
    if [[ -f "$REPO_ROOT/lib/python.sh" ]]; then
        # shellcheck disable=SC1091
        source "$REPO_ROOT/lib/python.sh"
    fi
    bash tests/run-tests.sh
    printf 'release.sh: tests OK\n'
else
    printf 'release.sh: --skip-tests (not recommended)\n'
fi

# Build release notes
NOTES_TMP="$(mktemp)"
{
    printf '## TG Agent Relay %s\n\n' "$TAG"
    if [[ -n "$NOTES_FILE" && -f "$NOTES_FILE" ]]; then
        cat "$NOTES_FILE"
        printf '\n'
    fi
    printf '### Install / upgrade local deploy\n\n'
    printf '```bash\n'
    printf 'cd /path/to/tg-agent-relay\n'
    printf 'git fetch --tags && git checkout %s\n' "$TAG"
    printf 'bash scripts/deploy-local.sh --ref %s\n' "$TAG"
    printf '```\n\n'
    printf 'Python: **3.14 preferred** (see lib/python.sh).\n'
    printf 'Full process: docs/RELEASING.md\n'
} > "$NOTES_TMP"

if (( DRY_RUN == 1 )); then
    printf 'release.sh: --dry-run — would tag %s and create GitHub release.\n' "$TAG"
    printf '--- notes preview ---\n'
    cat "$NOTES_TMP"
    rm -f "$NOTES_TMP"
    exit 0
fi

# Ensure VERSION is the release version (not -dev) on the tagged commit
if [[ "$FILE_VER" != "$VER" ]]; then
    printf '%s\n' "$VER" > VERSION
    git add VERSION
    git commit -m "chore(release): ${TAG}"
fi

git tag -a "$TAG" -m "Release ${TAG}"
git push origin HEAD
git push origin "$TAG"

if ! command -v gh >/dev/null 2>&1; then
    printf 'release.sh: tagged and pushed %s, but gh not found — create the GitHub Release manually.\n' "$TAG"
    rm -f "$NOTES_TMP"
    exit 0
fi

# Source archive for the release assets
ARCHIVE="/tmp/tg-agent-relay-${VER}.tar.gz"
git archive --format=tar.gz --prefix="tg-agent-relay-${VER}/" -o "$ARCHIVE" "$TAG"

gh release create "$TAG" \
    --title "TG Agent Relay ${TAG}" \
    --notes-file "$NOTES_TMP" \
    "$ARCHIVE"

rm -f "$NOTES_TMP"
printf 'release.sh: published %s\n' "$TAG"
printf '  https://github.com/tzervas/tg-agent-relay/releases/tag/%s\n' "$TAG"
printf 'Next: echo \"%s-dev\" > VERSION && commit; deploy with scripts/deploy-local.sh --ref %s\n' \
    "$(echo "$VER" | awk -F. '{print $1"."$2"."$3+1}')" "$TAG"

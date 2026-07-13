#!/bin/bash
# scripts/release.sh — Local-first tag + GitHub Release (workstation is source of truth).
#
# Preferred flow:
#   1. echo "0.6.1" > VERSION && git commit …
#   2. bash scripts/local-ci.sh --release     # full local gate
#   3. bash scripts/release.sh v0.6.1        # re-runs gate unless --skip-tests, then publish
#
# Does NOT depend on GitHub Actions. Remote release.yml is manual-only / optional.
#
# Usage:
#   bash scripts/release.sh v0.6.1
#   bash scripts/release.sh v0.6.1 --dry-run
#   bash scripts/release.sh v0.6.1 --skip-tests    # not recommended
#   bash scripts/release.sh v0.6.1 --notes-file RELEASE_NOTES.md
#   bash scripts/release.sh v0.6.1 --with-gitleaks
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TAG="${1:-}"
shift || true
DRY_RUN=0
SKIP_TESTS=0
NOTES_FILE=""
WITH_GITLEAKS=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --skip-tests) SKIP_TESTS=1; shift ;;
        --with-gitleaks) WITH_GITLEAKS=1; shift ;;
        --notes-file) NOTES_FILE="${2:-}"; shift 2 ;;
        -h | --help)
            sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            printf 'release.sh: unknown arg: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

if [[ -z "$TAG" || ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]]; then
    printf 'usage: release.sh vMAJOR.MINOR.PATCH [--dry-run] [--skip-tests] [--with-gitleaks] [--notes-file FILE]\n' >&2
    exit 2
fi

VER="${TAG#v}"
FILE_VER="$(tr -d '[:space:]' < VERSION 2>/dev/null || true)"
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

if git ls-remote --tags origin "refs/tags/${TAG}" 2>/dev/null | grep -q .; then
    printf 'release.sh: tag %s already exists on origin.\n' "$TAG" >&2
    exit 1
fi

# --- Local quality gate (source of truth; remote CI is manual-only) ---
if (( SKIP_TESTS == 0 )); then
    printf 'release.sh: running local-ci gate (full)…\n'
    GATE_ARGS=(--release)
    if (( WITH_GITLEAKS == 1 )); then
        GATE_ARGS+=(--with-gitleaks)
    fi
    bash "$REPO_ROOT/scripts/local-ci.sh" "${GATE_ARGS[@]}"
    printf 'release.sh: local-ci OK\n'
else
    printf 'release.sh: --skip-tests (not recommended) — still requiring clean tree\n'
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
    printf 'Built and published **locally** via `scripts/release.sh` '
    printf '(see `docs/RELEASING.md`). Python **3.14** preferred.\n'
} > "$NOTES_TMP"

if (( DRY_RUN == 1 )); then
    printf 'release.sh: --dry-run — would tag %s, push, and create GitHub release.\n' "$TAG"
    printf '--- notes preview ---\n'
    cat "$NOTES_TMP"
    rm -f "$NOTES_TMP"
    exit 0
fi

need_gh() {
    command -v gh >/dev/null 2>&1 || {
        printf 'release.sh: gh CLI required to publish GitHub Release from this workstation.\n' >&2
        exit 1
    }
}
need_gh

# Ensure VERSION is the release version (not -dev) on the tagged commit
if [[ "$FILE_VER" != "$VER" ]]; then
    printf '%s\n' "$VER" > VERSION
    git add VERSION
    git commit --no-gpg-sign -m "chore(release): ${TAG}"
fi

git tag -a "$TAG" -m "Release ${TAG}"

printf 'release.sh: pushing branch + tag to origin…\n'
git push origin HEAD
git push origin "$TAG"

# Source archive for the release assets (from local tag)
ARCHIVE="/tmp/tg-agent-relay-${VER}.tar.gz"
git archive --format=tar.gz --prefix="tg-agent-relay-${VER}/" -o "$ARCHIVE" "$TAG"

printf 'release.sh: creating GitHub Release %s via gh…\n' "$TAG"
if gh release view "$TAG" >/dev/null 2>&1; then
    printf 'release.sh: release %s already exists — uploading archive --clobber\n' "$TAG"
    gh release upload "$TAG" "$ARCHIVE" --clobber
else
    gh release create "$TAG" \
        --title "TG Agent Relay ${TAG}" \
        --notes-file "$NOTES_TMP" \
        "$ARCHIVE"
fi

rm -f "$NOTES_TMP"
printf 'release.sh: published %s\n' "$TAG"
printf '  https://github.com/tzervas/tg-agent-relay/releases/tag/%s\n' "$TAG"
NEXT_PATCH="$(printf '%s' "$VER" | awk -F. '{printf "%s.%s.%s", $1, $2, $3+1}')"
printf 'Next:\n'
printf '  echo \"%s-dev\" > VERSION && git add VERSION && git commit -m \"chore: bump VERSION to %s-dev\"\n' \
    "$NEXT_PATCH" "$NEXT_PATCH"
printf '  bash scripts/deploy-local.sh --ref %s\n' "$TAG"

#!/bin/bash
# scripts/deploy-local.sh - Update ~/.claude/telegram-bridge from this repo
# or a git ref, without clobbering secrets or runtime state.
#
# Usage:
#   bash scripts/deploy-local.sh                 # deploy current tree
#   bash scripts/deploy-local.sh --ref v0.6.0    # checkout ref in a temp worktree, deploy
#   bash scripts/deploy-local.sh --dest PATH
#   bash scripts/deploy-local.sh --dry-run
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${TG_RELAY_HOME:-$HOME/.claude/telegram-bridge}"
REF=""
DRY_RUN=0
SOURCE="$REPO_ROOT"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ref) REF="${2:-}"; shift 2 ;;
        --dest) DEST="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            printf 'deploy-local.sh: unknown arg: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

if [[ ! -d "$DEST" ]]; then
    printf 'deploy-local.sh: dest does not exist: %s\n' "$DEST" >&2
    printf '  Create it first (copy or go-live), or pass --dest.\n' >&2
    exit 1
fi

WORKDIR="$SOURCE"
CLEANUP=""
if [[ -n "$REF" ]]; then
    WORKDIR="$(mktemp -d /tmp/tg-relay-deploy.XXXXXX)"
    CLEANUP="$WORKDIR"
    # Prefer git archive for a clean tree at REF
    if git -C "$REPO_ROOT" rev-parse "$REF" >/dev/null 2>&1; then
        git -C "$REPO_ROOT" archive "$REF" | tar -x -C "$WORKDIR"
    else
        printf 'deploy-local.sh: unknown ref %s (fetch tags?)\n' "$REF" >&2
        rm -rf "$CLEANUP"
        exit 1
    fi
    printf 'deploy-local.sh: deploying ref %s from archive\n' "$REF"
else
    printf 'deploy-local.sh: deploying working tree %s\n' "$SOURCE"
fi

# rsync code; exclude secrets + runtime + VCS
RSYNC_FLAGS=(-a --delete)
if (( DRY_RUN == 1 )); then
    RSYNC_FLAGS+=(--dry-run -v)
fi

# --delete only under code dirs would be safer; we exclude protected paths
# and do not --delete at top level to avoid wiping unknown local files.
# Instead: sync known trees explicitly.

sync_tree() {
    local rel="$1"
    if [[ ! -e "$WORKDIR/$rel" ]]; then
        return 0
    fi
    mkdir -p "$DEST/$(dirname "$rel")"
    if [[ -d "$WORKDIR/$rel" ]]; then
        rsync "${RSYNC_FLAGS[@]}" \
            --exclude '.git/' \
            "$WORKDIR/$rel/" "$DEST/$rel/"
    else
        rsync "${RSYNC_FLAGS[@]}" "$WORKDIR/$rel" "$DEST/$rel"
    fi
}

# Top-level scripts and package trees
for rel in \
    adapters handlers lib providers docs scripts tests \
    hook-notify.sh hook-notify-grok.sh \
    install-hooks.sh install-grok-hooks.sh \
    relay-notify.sh tg-send.sh tg-poll.sh \
    go-live.sh watch-go-live.sh fetch-voices.sh \
    VERSION LICENSE README.md ROADMAP.md SETUP.md \
    relay.toml.example .env.example \
    .gitleaks.toml .pre-commit-config.yaml \
    .github
do
    sync_tree "$rel"
done

# Ensure executables
chmod +x "$DEST"/*.sh "$DEST"/adapters/*.sh "$DEST"/handlers/*.sh \
    "$DEST"/scripts/*.sh 2>/dev/null || true

# Write deploy stamp
if (( DRY_RUN == 0 )); then
    {
        printf 'deployed_at=%s\n' "$(date -Iseconds)"
        printf 'source=%s\n' "$SOURCE"
        printf 'ref=%s\n' "${REF:-working-tree}"
        if [[ -f "$WORKDIR/VERSION" ]]; then
            printf 'version=%s\n' "$(tr -d '[:space:]' < "$WORKDIR/VERSION")"
        fi
        git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null | sed 's/^/git=/'
    } > "$DEST/.deploy-stamp"
fi

[[ -n "$CLEANUP" ]] && rm -rf "$CLEANUP"

printf 'deploy-local.sh: %s → %s\n' "${REF:-working-tree}" "$DEST"
printf '  preserved: .env relay.toml .offset .metrics.log .usage/ .chats.d/ voices/\n'
if (( DRY_RUN == 0 )); then
    printf '  next: re-run install-hooks / install-grok-hooks if needed; restart poller\n'
    if [[ -f "$DEST/VERSION" ]]; then
        printf '  VERSION=%s\n' "$(tr -d '[:space:]' < "$DEST/VERSION")"
    fi
fi

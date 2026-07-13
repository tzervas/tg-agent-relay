#!/bin/bash
# scripts/local-ci.sh — Full local quality gate (replaces auto remote CI for day-to-day).
#
# Primary path: run everything on this workstation before push/release.
# GitHub Actions workflows are workflow_dispatch-only (manual); do not rely on
# them for release gates.
#
# Usage:
#   bash scripts/local-ci.sh              # full gate (lint + format-check + tests + rust)
#   bash scripts/local-ci.sh --quick      # ruff + rust only (no full test suite)
#   bash scripts/local-ci.sh --release    # full gate + require clean tree + VERSION present
#   bash scripts/local-ci.sh --skip-tests # lint/format/rust only
#   bash scripts/local-ci.sh --with-gitleaks  # also run gitleaks if installed
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

QUICK=0
SKIP_TESTS=0
RELEASE=0
WITH_GITLEAKS=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick) QUICK=1; shift ;;
        --skip-tests) SKIP_TESTS=1; shift ;;
        --release) RELEASE=1; shift ;;
        --with-gitleaks) WITH_GITLEAKS=1; shift ;;
        -h | --help)
            sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            printf 'local-ci.sh: unknown arg: %s\n' "$1" >&2
            exit 2
            ;;
    esac
done

need() {
    command -v "$1" >/dev/null 2>&1 || {
        printf 'local-ci.sh: missing %s — install it first\n' "$1" >&2
        exit 1
    }
}

step() { printf '\n== local-ci: %s ==\n' "$1"; }

FAIL=0
note_fail() {
    printf 'local-ci.sh: FAIL: %s\n' "$1" >&2
    FAIL=1
}

if (( RELEASE == 1 )); then
    step "release preflight (clean tree)"
    if [[ -n "$(git status --porcelain)" ]]; then
        git status -sb
        note_fail "working tree not clean"
    fi
    if [[ ! -f VERSION ]]; then
        note_fail "VERSION file missing"
    fi
fi

# --- Python env ---
step "uv sync (Python 3.14)"
need uv
uv python install 3.14 2>/dev/null || true
uv sync --all-groups

if [[ -x "$ROOT/.venv/bin/python" ]]; then
    export RELAY_PYTHON="$ROOT/.venv/bin/python"
fi
# shellcheck disable=SC1091
[[ -f lib/python.sh ]] && source lib/python.sh || true

# --- Ruff ---
step "ruff check"
if ! uv run ruff check tg_agent_relay providers lib tests; then
    note_fail "ruff check"
fi

step "ruff format --check"
if ! uv run ruff format --check tg_agent_relay providers lib tests; then
    note_fail "ruff format --check (run: bash scripts/dev.sh format)"
fi

# --- Rust MSRV ---
step "rust MSRV / workspace"
if command -v rustup >/dev/null 2>&1 && command -v cargo >/dev/null 2>&1; then
    if ! bash scripts/dev.sh rust-check; then
        note_fail "rust-check"
    fi
else
    printf 'local-ci.sh: rustup/cargo missing — skip rust-check (install for full gate)\n'
    if (( RELEASE == 1 )); then
        note_fail "rust toolchain required for --release gate"
    fi
fi

# --- Optional gitleaks ---
if (( WITH_GITLEAKS == 1 )); then
    step "gitleaks"
    if command -v gitleaks >/dev/null 2>&1; then
        if ! gitleaks detect --source . --verbose; then
            note_fail "gitleaks"
        fi
    else
        printf 'local-ci.sh: gitleaks not installed — skip (pipx/brew install gitleaks)\n'
        if (( RELEASE == 1 )); then
            note_fail "gitleaks required when --with-gitleaks and --release"
        fi
    fi
fi

# --- Tests ---
if (( SKIP_TESTS == 1 || QUICK == 1 )); then
    step "tests skipped (--quick or --skip-tests)"
else
    step "offline tests (tests/run-tests.sh)"
    if ! bash tests/run-tests.sh; then
        note_fail "offline tests"
    fi
fi

# --- Package smoke ---
step "package CLI smoke"
if ! uv run python -m tg_agent_relay.cli version; then
    note_fail "tg_agent_relay.cli version"
fi

if (( FAIL != 0 )); then
    printf '\nlocal-ci.sh: FAILED — fix issues above before push/release\n' >&2
    exit 1
fi

printf '\nlocal-ci.sh: OK — all local gates passed\n'
if (( RELEASE == 1 )); then
    printf 'local-ci.sh: ready for scripts/release.sh vX.Y.Z\n'
fi
exit 0

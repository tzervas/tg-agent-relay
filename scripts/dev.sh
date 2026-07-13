#!/bin/bash
# scripts/dev.sh - UV + Ruff (+ optional Cargo) developer entrypoints.
#
#   bash scripts/dev.sh sync      # uv sync (Python 3.14 env + dev deps)
#   bash scripts/dev.sh lint      # ruff check
#   bash scripts/dev.sh format    # ruff format
#   bash scripts/dev.sh test      # offline tests via uv run
#   bash scripts/dev.sh check     # lint + test
#   bash scripts/dev.sh rust-check  # cargo fmt --check + clippy (when crates exist)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

need() {
    command -v "$1" >/dev/null 2>&1 || {
        printf 'dev.sh: missing %s — install it first\n' "$1" >&2
        exit 1
    }
}

cmd="${1:-check}"
shift || true

case "$cmd" in
    sync)
        need uv
        uv python install 3.14 2>/dev/null || true
        uv sync --all-groups "$@"
        printf 'dev.sh: uv env ready (see .venv). Prefer: uv run …\n'
        ;;
    lint)
        need uv
        uv run ruff check tg_agent_relay providers lib tests "$@"
        ;;
    format | fmt)
        need uv
        uv run ruff format tg_agent_relay providers lib tests "$@"
        uv run ruff check --fix tg_agent_relay providers lib tests || true
        ;;
    test)
        need uv
        # Prefer uv run python so deps (pygments, ruff) resolve from the project env
        if [[ -x .venv/bin/python ]]; then
            export RELAY_PYTHON="$ROOT/.venv/bin/python"
        fi
        # shellcheck disable=SC1091
        [[ -f lib/python.sh ]] && source lib/python.sh || true
        bash tests/run-tests.sh "$@"
        ;;
    check)
        need uv
        uv run ruff check tg_agent_relay providers lib tests
        if [[ -x .venv/bin/python ]]; then
            export RELAY_PYTHON="$ROOT/.venv/bin/python"
        fi
        # shellcheck disable=SC1091
        [[ -f lib/python.sh ]] && source lib/python.sh || true
        bash tests/run-tests.sh
        ;;
    rust-check)
        need cargo
        need rustup
        rustup show
        # Workspace root always has Cargo.toml; only fmt/clippy when members exist.
        members="$(cargo metadata --no-deps --format-version 1 2>/dev/null \
            | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("workspace_members") or []))' \
            2>/dev/null || echo 0)"
        if [[ "${members:-0}" -eq 0 ]]; then
            printf 'dev.sh: no workspace members yet (MSRV pin only) — cargo metadata ok\n'
            cargo metadata --no-deps -q
            exit 0
        fi
        cargo fmt --all -- --check
        cargo clippy --workspace --all-targets -- -D warnings
        ;;
    -h | --help | help)
        sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
        ;;
    *)
        printf 'dev.sh: unknown command %s (sync|lint|format|test|check|rust-check)\n' "$cmd" >&2
        exit 2
        ;;
esac

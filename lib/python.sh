#!/bin/bash
# lib/python.sh - Resolve the Python interpreter for TG Agent Relay.
#
# Preference (unless RELAY_PYTHON is set explicitly):
#   1. python3.14   — preferred / default target
#   2. python3.13   — acceptable when 3.14 is unavailable
#   3. python3      — fallback (must be ≥ 3.11 for tomllib)
#
# Usage (source, never execute):
#   source "$BRIDGE_DIR/lib/python.sh"
#   "$RELAY_PYTHON" lib/foo.py …
#
# Or:  relay_python -c 'print(1)'
#      relay_python lib/provider_hook.py grok
set -u

# relay_python_resolve → sets RELAY_PYTHON to an absolute or PATH command.
relay_python_resolve() {
    local cand ver major minor
    if [[ -n "${RELAY_PYTHON:-}" ]]; then
        if command -v "$RELAY_PYTHON" >/dev/null 2>&1 || [[ -x "$RELAY_PYTHON" ]]; then
            return 0
        fi
        # Invalid override — fall through and re-resolve
        RELAY_PYTHON=""
    fi
    for cand in python3.14 python3.13 python3; do
        if ! command -v "$cand" >/dev/null 2>&1; then
            continue
        fi
        ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || continue
        major="${ver%%.*}"
        minor="${ver#*.}"
        # tomllib requires 3.11+
        if [[ "$major" -gt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -ge 11 ]]; }; then
            RELAY_PYTHON="$cand"
            return 0
        fi
    done
    RELAY_PYTHON=""
    return 1
}

# relay_python [args...] — run with the resolved interpreter (exit 127 if none).
relay_python() {
    if [[ -z "${RELAY_PYTHON:-}" ]]; then
        relay_python_resolve || {
            printf 'relay: no suitable Python found (need 3.14 preferred, ≥3.11 minimum)\n' >&2
            return 127
        }
    fi
    "$RELAY_PYTHON" "$@"
}

# Auto-resolve on source so callers can use $RELAY_PYTHON directly.
relay_python_resolve || true

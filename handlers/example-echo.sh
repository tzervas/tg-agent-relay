#!/bin/bash
# handlers/example-echo.sh - Minimal relay-handled-command EXAMPLE.
#
# Not a real feature and not wired into relay.toml.example - this exists
# only to prove the dispatch_command() "mode = relay" seam in
# tests/run-tests.sh (see handlers/README.md for the real contract a
# future handler follows). Writes its received text to a marker file
# instead of actually messaging Telegram, so tests can assert it ran
# without any network dependency.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEXT="${1:-}"

printf '%s' "$TEXT" > "$BRIDGE_DIR/.example-echo-received" 2>/dev/null || true
exit 0

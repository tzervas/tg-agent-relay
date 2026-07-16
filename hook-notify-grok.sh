#!/bin/bash
# hook-notify-grok.sh - Stable Grok hook entry point (shim).
#
# Point Grok's hook command at this script (install-grok-hooks.sh does).
# Forwards stdin JSON + exit code to adapters/grok.sh via exec.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/exec-env.sh" ]] && source "$BRIDGE_DIR/lib/exec-env.sh"
exec "$BRIDGE_DIR/adapters/grok.sh"

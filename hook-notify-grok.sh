#!/bin/bash
# hook-notify-grok.sh - Stable Grok hook entry point (shim).
#
# Point Grok's hook command at this script (install-grok-hooks.sh does).
# Forwards stdin JSON + exit code to adapters/grok.sh via exec.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$BRIDGE_DIR/adapters/grok.sh"

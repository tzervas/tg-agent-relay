#!/bin/bash
# hook-notify.sh - Backward-compatible Claude Code hook shim.
#
# Wired into ~/.claude/settings.json under hooks.SubagentStop /
# hooks.Notification (this exact path, unchanged - see the "live bridge"
# note in README.md). Historically this script itself parsed the Claude
# Code hook JSON; that logic now lives in adapters/claude-code.sh (one
# adapter among possibly several - see adapters/README.md), so this file
# is now a thin, stable entry point that just forwards to it. Anything
# invoking `hook-notify.sh` - the settings.json hook wiring, a manual
# test, another script - keeps working unchanged; only the internals moved.
#
# `exec` (not a plain call) so stdin (the hook's JSON payload) and the
# adapter's own exit code pass straight through with no extra process.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$BRIDGE_DIR/adapters/claude-code.sh"

#!/bin/bash
# adapters/generic-example.sh - Minimal adapter STUB/template.
#
# Not wired into anything by default - copy this file to
# `adapters/<your-harness>.sh`, fill in the two marked sections, and wire
# it into your harness's own hook/event mechanism. See adapters/README.md
# for the full how-to.
#
# This stub: reads one line of free text from stdin (e.g. `echo "build
# finished" | adapters/generic-example.sh`), labels it with this adapter's
# name, and forwards it through relay-notify.sh - a working, if trivial,
# harness integration you can run right now to see the whole pipeline.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- 1. Read your harness's event -----------------------------------------
# Replace this with however your harness delivers events: stdin JSON
# (`jq` it - see adapters/claude-code.sh for a full example), argv, a
# tailed log line, a webhook body, etc.
LINE="$(cat 2>/dev/null || true)"
[[ -z "$LINE" ]] && exit 0

# --- 2. Turn it into a readable summary and hand off ----------------------
# Simplest form: relay-notify.sh --raw "<your fully-formatted text>".
# (Or use --label "<name>" "<detail>" to let relay.toml's [generic] prefix
# apply - see relay-notify.sh --help/header.)
SUMMARY="generic-example: ${LINE}"

"$BRIDGE_DIR/relay-notify.sh" --raw "$SUMMARY" >/dev/null 2>&1

exit 0

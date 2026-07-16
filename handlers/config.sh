#!/bin/bash
# handlers/config.sh - Relay-handled /config: view/change allowlisted relay.toml
# settings from Telegram (zero model tokens). See lib/remote_config.py.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-common.sh" ]] && source "$BRIDGE_DIR/lib/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }

TEXT="${1:-}"
TOML="$BRIDGE_DIR/relay.toml"

MSG="$(relay_python "$BRIDGE_DIR/lib/remote_config.py" --toml "$TOML" handle "$TEXT" 2>/dev/null)" || MSG="❌ Config handler failed (python/tomllib unavailable)."

emit_metric "config" "reply" ""
"$BRIDGE_DIR/relay-notify.sh" --raw "$MSG" >/dev/null 2>&1
exit 0
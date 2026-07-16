#!/bin/bash
# handlers/config.sh - Relay-handled /config: view/change allowlisted relay.toml
# settings from Telegram (zero model tokens). See lib/remote_config.py.
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/exec-env.sh" ]] && source "$BRIDGE_DIR/lib/exec-env.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-common.sh" ]] && source "$BRIDGE_DIR/lib/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }

TEXT="${1:-}"
TOML="$BRIDGE_DIR/relay.toml"

_sanitize_err_line() {
    local line="${1:-}"
    line="${line//$'\r'/}"
    line="${line//$'\n'/ }"
    line="$(printf '%s' "$line" | sed -E \
        's/(BOT_TOKEN|ALLOWED_(USER|CHAT)_ID|api[_-]?key|secret|password)[^[:space:]]*/[redacted]/gi')"
    if ((${#line} > 200)); then
        line="${line:0:200}..."
    fi
    printf '%s' "$line"
}

ERRFILE="$(mktemp "${TMPDIR:-/tmp}/relay-config-err-XXXXXX")"
trap 'rm -f "$ERRFILE"' EXIT

if MSG="$(relay_python "$BRIDGE_DIR/lib/remote_config.py" --toml "$TOML" handle "$TEXT" 2>"$ERRFILE")"; then
    :
else
    errline="$(_sanitize_err_line "$(head -n 1 "$ERRFILE" 2>/dev/null || true)")"
    if [[ -n "$errline" ]]; then
        MSG="Config handler failed: ${errline}"
    else
        MSG="Config handler failed (python unavailable or import error)."
    fi
    MSG="❌ ${MSG#❌ }"
fi

emit_metric "config" "reply" ""
"$BRIDGE_DIR/relay-notify.sh" --raw "$MSG" >/dev/null 2>&1
exit 0
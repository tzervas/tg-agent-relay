#!/bin/bash
# handlers/ext.sh — Zero-token extension bus from Telegram.
#
# Invoke relay-native tools without a model:
#   /ext list
#   /ext echo hello world
#   /ext provider_catalog
#   /ext adk_probe
#
# Wire in relay.toml:
#   [commands.ext]
#   keyword = "ext"
#   slash = "/ext"
#   mode = "relay"
#   handler = "handlers/ext.sh"
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-common.sh" ]] && source "$BRIDGE_DIR/lib/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }

# RELAY_TEXT is the full inbound line (may include /ext prefix)
TEXT="${RELAY_TEXT:-$*}"
# Strip leading /ext or ext keyword
ARGS="$(printf '%s' "$TEXT" | sed -E 's|^[[:space:]]*/?ext[[:space:]]+||I; s|^[[:space:]]+||')"

if [[ -z "$ARGS" || "$ARGS" == "list" || "$ARGS" == "help" ]]; then
    OUT="$(
        relay_python - <<'PY'
from tg_agent_relay.extensions import call_extension, ensure_builtin_extensions
ensure_builtin_extensions()
# ensure ADK probes registered if provider loaded
try:
    import providers  # noqa: F401
except Exception:
    pass
r = call_extension("relay_ext_list", {})
exts = r.get("extensions") or []
lines = ["🧩 Relay extensions (no model tokens):"]
for e in exts:
    lines.append(f"  • {e['name']} — {e.get('description','')[:80]}")
lines.append("")
lines.append("Usage: /ext <name> [args…]   e.g. /ext echo hi")
lines.append("       /ext call <name> {json}")
print("\n".join(lines))
PY
    )"
    "$BRIDGE_DIR/relay-notify.sh" --raw "$OUT" >/dev/null 2>&1 || printf '%s\n' "$OUT"
    emit_metric "ext" "list" ""
    exit 0
fi

# /ext call name {...json...}
# /ext name rest-as-text
OUT="$(
    NAME_ARGS="$ARGS" relay_python - <<'PY'
import json, os, shlex
from tg_agent_relay.extensions import call_extension, ensure_builtin_extensions

ensure_builtin_extensions()
try:
    import providers  # noqa: F401
except Exception:
    pass

raw = os.environ.get("NAME_ARGS", "").strip()
parts = shlex.split(raw) if raw else []
if not parts:
    print("Usage: /ext <name> [text] | /ext call <name> '{json}'")
    raise SystemExit(0)

if parts[0].lower() == "call" and len(parts) >= 2:
    name = parts[1]
    # Map short names
    aliases = {
        "list": "relay_ext_list",
        "echo": "relay_ext_echo",
        "providers": "relay_provider_catalog",
        "adk": "relay_adk_probe",
        "adk_probe": "relay_adk_probe",
        "adk_mcp": "relay_adk_mcp_config",
    }
    name = aliases.get(name, name)
    if not name.startswith("relay_"):
        name = f"relay_ext_{name}" if name in ("echo", "list") else name
    arg_s = " ".join(parts[2:]) if len(parts) > 2 else "{}"
    try:
        args = json.loads(arg_s) if arg_s.strip() else {}
    except json.JSONDecodeError:
        args = {"text": arg_s}
    r = call_extension(name, args if isinstance(args, dict) else {})
    print(json.dumps(r, indent=2, default=str))
else:
    short = parts[0].lower()
    aliases = {
        "list": "relay_ext_list",
        "echo": "relay_ext_echo",
        "providers": "relay_provider_catalog",
        "catalog": "relay_provider_catalog",
        "adk": "relay_adk_probe",
        "adk_probe": "relay_adk_probe",
        "adk_mcp": "relay_adk_mcp_config",
    }
    name = aliases.get(short, short)
    if name in ("relay_ext_echo", "echo") or short == "echo":
        r = call_extension("relay_ext_echo", {"text": " ".join(parts[1:])})
    elif name == "relay_ext_list" or short == "list":
        r = call_extension("relay_ext_list", {})
    elif short in ("providers", "catalog"):
        r = call_extension("relay_provider_catalog", {"filter": "all"})
    elif short in ("adk", "adk_probe"):
        r = call_extension("relay_adk_probe", {})
    elif short == "adk_mcp":
        r = call_extension("relay_adk_mcp_config", {})
    else:
        # treat first token as full extension name, rest as text
        r = call_extension(name, {"text": " ".join(parts[1:])} if len(parts) > 1 else {})
    print(json.dumps(r, indent=2, default=str) if isinstance(r, dict) else r)
PY
)"
"$BRIDGE_DIR/relay-notify.sh" --raw "$OUT" >/dev/null 2>&1 || printf '%s\n' "$OUT"
emit_metric "ext" "call" ""
exit 0

#!/bin/bash
# scripts/doctor-inbound.sh — Diagnose inbound route-to-agent health.
#
# Prints:
#   - routing.default_backend
#   - per-FIFO agent reader counts (backend-fifo-reader / tgar-session@)
#   - Monitor commands for fleet and cabal (and any other fifo backends)
#
# Exit codes:
#   0 — default_backend FIFO has ≥1 agent reader (or default is non-fifo)
#   1 — default_backend FIFO has zero agent readers (orphan sink)
#   2 — usage / missing tools
#
# Keepalives (ensure-inbound RDWR holders) are NOT agent readers. A FIFO with
# only keepalives is an orphan: tg-poll may write successfully while no agent
# Monitor ever sees the line. Untagged messages need a Monitor on the
# default_backend FIFO — for multi-agent orch prefer default_backend = "fleet".
#
# Usage:
#   bash scripts/doctor-inbound.sh [--bridge-dir PATH]
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bridge-dir) BRIDGE_DIR="${2:-}"; shift 2 ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) printf 'doctor-inbound.sh: unknown arg: %s\n' "$1" >&2; exit 2 ;;
    esac
done

BRIDGE_DIR="$(cd "$BRIDGE_DIR" && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/exec-env.sh" ]] && source "$BRIDGE_DIR/lib/exec-env.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }

if ! command -v jq >/dev/null 2>&1; then
    printf 'doctor-inbound: ERROR jq is required\n' >&2
    exit 2
fi

TOML="$BRIDGE_DIR/relay.toml"
if [[ -f "$TOML" ]] && declare -f load_relay_config >/dev/null 2>&1; then
    load_relay_config "$TOML"
else
    RELAY_CONFIG_JSON="${RELAY_CONFIG_JSON:-{}}"
fi

DEFAULT_BACKEND="$(cfg_get '.routing.default_backend' "")"
READER_HELPER="$BRIDGE_DIR/lib/fifo_agent_readers.py"

printf 'doctor-inbound: bridge_dir=%s\n' "$BRIDGE_DIR"
if [[ -f "$TOML" ]]; then
    printf 'doctor-inbound: config=%s\n' "$TOML"
else
    printf 'doctor-inbound: config=(missing %s — using empty/defaults)\n' "$TOML"
fi
printf 'doctor-inbound: default_backend=%s\n' "${DEFAULT_BACKEND:-"(unset)"}"
printf '\n'

expand_path() {
    local p="$1"
    p="${p/#\~/$HOME}"
    printf '%s' "$p"
}

# True if an agent Monitor holds the FIFO (prefer poll.fifo_has_agent_reader).
agent_reader_present() {
    local fifo="$1"
    if RELAY_BRIDGE_DIR="$BRIDGE_DIR" PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$BRIDGE_DIR" \
        relay_python -c '
import os, sys
from pathlib import Path
fifo = sys.argv[1]
root = Path(os.environ.get("RELAY_BRIDGE_DIR") or ".")
sys.path.insert(0, str(root))
try:
    from tg_agent_relay.poll import fifo_has_agent_reader
    sys.exit(0 if fifo_has_agent_reader(fifo) else 1)
except Exception:
    sys.exit(2)
' "$fifo" 2>/dev/null; then
        return 0
    fi
    if [[ -f "$READER_HELPER" ]]; then
        local n
        n="$(relay_python "$READER_HELPER" "$fifo" 2>/dev/null \
            | awk -F= '/^count=/{print $2; found=1} END{if(!found) print 0}')"
        [[ "${n:-0}" != "0" ]] && return 0
    fi
    return 1
}

agent_reader_count() {
    local fifo="$1"
    if [[ ! -f "$READER_HELPER" ]]; then
        if agent_reader_present "$fifo"; then
            printf '1+'
        else
            printf '0'
        fi
        return
    fi
    relay_python "$READER_HELPER" "$fifo" 2>/dev/null \
        | awk -F= '/^count=/{print $2; found=1} END{if(!found) print 0}'
}

agent_reader_pids() {
    local fifo="$1"
    if [[ ! -f "$READER_HELPER" ]]; then
        return
    fi
    relay_python "$READER_HELPER" "$fifo" 2>/dev/null \
        | awk -F= '/^pids=/{print $2}'
}

monitor_cmd() {
    local fifo="$1"
    printf '%s/adapters/backend-fifo-reader.sh %s' "$BRIDGE_DIR" "$fifo"
}

declare -a BACKEND_IDS=()
declare -A BACKEND_FIFO=()
declare -A BACKEND_DELIVERY=()

if [[ -n "${RELAY_CONFIG_JSON:-}" ]]; then
    while IFS=$'\t' read -r bid fifo delivery; do
        [[ -n "$bid" ]] || continue
        delivery="${delivery:-fifo}"
        BACKEND_IDS+=("$bid")
        BACKEND_FIFO["$bid"]="$(expand_path "$fifo")"
        BACKEND_DELIVERY["$bid"]="$delivery"
    done < <(printf '%s' "$RELAY_CONFIG_JSON" | jq -r '
        (.backends // {}) | to_entries[]
        | [.key, (.value.fifo // ""), (.value.delivery // "fifo")]
        | @tsv')
fi

# Ensure fleet + cabal appear even if not in config (Monitor command section).
for need in fleet cabal; do
    found=0
    for bid in "${BACKEND_IDS[@]+"${BACKEND_IDS[@]}"}"; do
        if [[ "$bid" == "$need" ]]; then
            found=1
            break
        fi
    done
    if (( found == 0 )); then
        BACKEND_IDS+=("$need")
        BACKEND_FIFO["$need"]="$(expand_path "$HOME/.grok/telegram-bridge/sessions/${need}.fifo")"
        BACKEND_DELIVERY["$need"]="fifo"
    fi
done

printf '== FIFO agent readers ==\n'
DEFAULT_FIFO=""
DEFAULT_DELIVERY=""
DEFAULT_HAS=0

for bid in "${BACKEND_IDS[@]+"${BACKEND_IDS[@]}"}"; do
    delivery="${BACKEND_DELIVERY[$bid]:-fifo}"
    fifo="${BACKEND_FIFO[$bid]:-}"
    if [[ "$bid" == "$DEFAULT_BACKEND" ]]; then
        DEFAULT_DELIVERY="$delivery"
        DEFAULT_FIFO="$fifo"
    fi
    if [[ "$delivery" != "fifo" ]]; then
        printf '  backend=%-12s delivery=%-6s (no FIFO — skip reader check)\n' \
            "$bid" "$delivery"
        continue
    fi
    if [[ -z "$fifo" || "$fifo" == "stdout" ]]; then
        printf '  backend=%-12s delivery=fifo  fifo=(unset)\n' "$bid"
        continue
    fi

    count="$(agent_reader_count "$fifo")"
    pids="$(agent_reader_pids "$fifo")"
    exists="missing"
    [[ -p "$fifo" ]] && exists="fifo"
    [[ -e "$fifo" && ! -p "$fifo" ]] && exists="not-a-fifo"
    status="ok"
    has=0
    if agent_reader_present "$fifo"; then
        has=1
        if [[ "$count" == "0" ]]; then
            count="1+"
        fi
    else
        count="0"
        status="ORPHAN (no agent reader)"
    fi
    printf '  backend=%-12s fifo=%s (%s) agent_readers=%s' \
        "$bid" "$fifo" "$exists" "$count"
    if [[ -n "$pids" ]]; then
        printf ' pids=%s' "$pids"
    fi
    printf '  [%s]\n' "$status"
    if [[ "$bid" == "$DEFAULT_BACKEND" ]]; then
        DEFAULT_HAS="$has"
    fi
done

printf '\n== Monitor commands (attach in agent harness) ==\n'
for bid in fleet cabal; do
    fifo="${BACKEND_FIFO[$bid]:-}"
    [[ -n "$fifo" ]] || continue
    printf '  # %s\n' "$bid"
    printf '  %s\n' "$(monitor_cmd "$fifo")"
done

for bid in "${BACKEND_IDS[@]+"${BACKEND_IDS[@]}"}"; do
    [[ "$bid" == "fleet" || "$bid" == "cabal" ]] && continue
    delivery="${BACKEND_DELIVERY[$bid]:-fifo}"
    [[ "$delivery" == "fifo" ]] || continue
    fifo="${BACKEND_FIFO[$bid]:-}"
    [[ -n "$fifo" && "$fifo" != "stdout" ]] || continue
    printf '  # %s\n' "$bid"
    printf '  %s\n' "$(monitor_cmd "$fifo")"
done

printf '\n== Recommendation ==\n'
printf '  Multi-agent orch: set routing.default_backend = "fleet" (general Grok).\n'
printf '  cabal is the L0 coding leaf — route with @cabal …\n'
printf '  Untagged messages go to default_backend and NEED a Monitor on that FIFO.\n'
printf '  Keepalives alone are not enough (writes succeed; agent never sees lines).\n'
printf '  Start keepalives/poll: bash scripts/ensure-inbound.sh --bridge-dir %s\n' \
    "$BRIDGE_DIR"

if [[ -z "$DEFAULT_BACKEND" ]]; then
    printf '\ndoctor-inbound: WARN default_backend unset — unprefixed messages may not route\n'
    exit 0
fi

if [[ "$DEFAULT_DELIVERY" != "fifo" ]]; then
    printf '\ndoctor-inbound: OK default_backend=%s delivery=%s (non-fifo)\n' \
        "$DEFAULT_BACKEND" "${DEFAULT_DELIVERY:-stdout}"
    exit 0
fi

if [[ -z "$DEFAULT_FIFO" ]]; then
    printf '\ndoctor-inbound: FAIL default_backend=%s has no fifo path\n' "$DEFAULT_BACKEND"
    exit 1
fi

if [[ "$DEFAULT_HAS" == "0" ]]; then
    printf '\ndoctor-inbound: FAIL default_backend=%s fifo has no agent reader\n' \
        "$DEFAULT_BACKEND"
    printf '  Attach Monitor:\n    %s\n' "$(monitor_cmd "$DEFAULT_FIFO")"
    printf '  Deeper report: bash %s/scripts/inbound-health.sh --bridge-dir %s\n' \
        "$BRIDGE_DIR" "$BRIDGE_DIR"
    exit 1
fi

printf '\ndoctor-inbound: OK default_backend=%s has agent reader\n' \
    "$DEFAULT_BACKEND"
exit 0

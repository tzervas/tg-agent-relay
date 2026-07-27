#!/bin/bash
# handlers/thread.sh - Relay-handled /thread commands (forum topics, zero tokens).
#
# Subcommands:
#   /thread list | here | bind | ensure
#
# Env from poller: RELAY_CHAT_ID, RELAY_THREAD_ID
set -u

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-config.sh" ]] && source "$BRIDGE_DIR/lib/relay-config.sh"
if declare -f load_relay_config >/dev/null 2>&1; then
    load_relay_config "$BRIDGE_DIR/relay.toml"
else
    cfg_get() { printf '%s' "$2"; }
fi
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/relay-common.sh" ]] && source "$BRIDGE_DIR/lib/relay-common.sh"
declare -f emit_metric >/dev/null 2>&1 || emit_metric() { :; }
# shellcheck disable=SC1091
[[ -f "$BRIDGE_DIR/lib/python.sh" ]] && source "$BRIDGE_DIR/lib/python.sh"
declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }

TEXT="${1:-}"
CHAT_ID="${RELAY_CHAT_ID:-}"
THREAD_ID="${RELAY_THREAD_ID:-}"
OVERLAY_DIR="$BRIDGE_DIR/.chats.d"
if [[ -n "${RELAY_CHATS_OVERLAY:-}" ]]; then
    OVERLAY="$RELAY_CHATS_OVERLAY"
    OVERLAY_DIR="$(dirname "$OVERLAY")"
else
    OVERLAY="$OVERLAY_DIR/bindings.json"
fi

ARGS="$TEXT"
ARGS="${ARGS#/thread}"
ARGS="${ARGS#thread}"
ARGS="${ARGS#"${ARGS%%[![:space:]]*}"}"

reply() {
    export RELAY_CHAT_ID="$CHAT_ID"
    export RELAY_THREAD_ID="$THREAD_ID"
    "$BRIDGE_DIR/relay-notify.sh" --raw "$1" >/dev/null 2>&1
}

_threads_enabled() {
    relay_python -c "
from tg_agent_relay.config import load_config
from tg_agent_relay.threads import threads_enabled
import sys
from pathlib import Path
b=Path(sys.argv[1])
print('1' if threads_enabled(load_config(b/'relay.toml', bridge_dir=b)) else '0')
" "$BRIDGE_DIR" 2>/dev/null | grep -q 1
}

_parse_kv() {
    # Sets globals: KV_SESSION KV_PROJECT KV_WS KV_PLATFORM KV_HANDLE KV_BACKEND
    KV_SESSION="" KV_PROJECT="" KV_WS="" KV_PLATFORM="" KV_HANDLE="" KV_BACKEND=""
    local token
    for token in $*; do
        case "$token" in
            session=*) KV_SESSION="${token#session=}" ;;
            repo=*) KV_PROJECT="${token#repo=}" ;;
            project=*) KV_PROJECT="${token#project=}" ;;
            ws=*) KV_WS="${token#ws=}" ;;
            workstream=*) KV_WS="${token#workstream=}" ;;
            platform=*) KV_PLATFORM="${token#platform=}" ;;
            handle=*) KV_HANDLE="${token#handle=}" ;;
            backend=*) KV_BACKEND="${token#backend=}" ;;
        esac
    done
}

cmd="${ARGS%% *}"
rest="${ARGS#"$cmd"}"
rest="${rest#"${rest%%[![:space:]]*}"}"
[[ -z "$cmd" ]] && cmd="list"

case "$cmd" in
    list | ls | "")
        if ! _threads_enabled; then
            reply "Threads disabled. Set [threads] enabled = true in relay.toml"
            exit 0
        fi
        MSG="$(relay_python -c "
import json, os
from pathlib import Path
from tg_agent_relay.config import load_config
cfg = load_config(Path('$BRIDGE_DIR')/'relay.toml', bridge_dir=Path('$BRIDGE_DIR'))
rows = [c for c in (cfg.get('chats') or []) if isinstance(c, dict) and c.get('session')]
print('🧵 Thread bindings (session topics)')
if not rows:
    print('(none — use /thread ensure session=<id> repo=<slug>)')
for c in rows:
    print(f\"  chat={c.get('chat_id')} thread={c.get('thread_id','')} session={c.get('session','')} project={c.get('project','')} ws={c.get('workstream','')}\")
" 2>/dev/null)" || MSG="(python error listing threads)"
        reply "$MSG"
        emit_metric "thread" "list" ""
        ;;
    here)
        MSG="📍 Thread context
chat_id=${CHAT_ID:-unknown} thread_id=${THREAD_ID:-none}"
        if relay_python -c "import tg_agent_relay.threads" >/dev/null 2>&1; then
            RES="$(RELAY_CHAT_ID="$CHAT_ID" RELAY_THREAD_ID="$THREAD_ID" \
                relay_python -m tg_agent_relay.threads resolve-outbound --bridge-dir "$BRIDGE_DIR" 2>/dev/null)" || RES=""
            if [[ -n "$RES" ]]; then
                MSG+=$'\n'"resolve: ${RES}"
            fi
        fi
        reply "$MSG"
        emit_metric "thread" "here" ""
        ;;
    bind)
        _parse_kv $rest
        if [[ -z "$KV_SESSION" ]]; then
            reply "Usage: /thread bind session=<id> [repo=<slug>] [ws=<slug>] [platform=grok] [handle=@…]"
            exit 0
        fi
        if [[ -z "$CHAT_ID" ]]; then
            reply "Send /thread bind from the target forum topic (need chat_id)."
            exit 0
        fi
        relay_python -c "
from pathlib import Path
from tg_agent_relay.config import load_config
from tg_agent_relay import threads
import os, sys
b = Path('$BRIDGE_DIR')
overlay = Path(os.environ.get('RELAY_CHATS_OVERLAY', b/'.chats.d'/'bindings.json'))
row = threads.binding_row(
    chat_id='$CHAT_ID',
    thread_id='${THREAD_ID:-}' or None,
    session='$KV_SESSION',
    project='$KV_PROJECT',
    workstream='$KV_WS',
    platform='$KV_PLATFORM',
    handle='$KV_HANDLE',
    backend='$KV_BACKEND',
)
threads.upsert_overlay_binding(overlay, row)
print('ok')
" 2>/dev/null && reply "✅ Bound thread session=\`${KV_SESSION}\` here (chat=${CHAT_ID} thread=${THREAD_ID:-none})" \
            || reply "Failed to bind (check overlay permissions)."
        emit_metric "thread" "bind" "session=$KV_SESSION"
        ;;
    ensure)
        _parse_kv $rest
        if [[ -z "$KV_SESSION" ]]; then
            reply "Usage: /thread ensure session=<id> [repo=<slug>] [ws=<slug>] [platform=grok]
Creates forum topic (when auto_create) in platform chat or current chat."
            exit 0
        fi
        if ! _threads_enabled; then
            reply "[threads] not enabled in relay.toml"
            exit 0
        fi
        # shellcheck disable=SC1091
        [[ -f "$BRIDGE_DIR/.env" ]] && set -a && source "$BRIDGE_DIR/.env" && set +a
        BOT_TOKEN="${BOT_TOKEN:-}"
        ALLOWED_CHAT_ID="${ALLOWED_CHAT_ID:-}"
        TARGET_CHAT="$CHAT_ID"
        if [[ -n "$KV_PLATFORM" ]]; then
            TARGET_CHAT="$(relay_python -c "
from tg_agent_relay.config import load_config
from tg_agent_relay.threads import platform_chat_id
from pathlib import Path
b=Path('$BRIDGE_DIR')
print(platform_chat_id(load_config(b/'relay.toml', bridge_dir=b), '$KV_PLATFORM'))
" 2>/dev/null)" || TARGET_CHAT=""
        fi
        [[ -z "$TARGET_CHAT" ]] && TARGET_CHAT="$CHAT_ID"
        if [[ -z "$TARGET_CHAT" ]]; then
            reply "Need chat_id (send from forum) or platform=<name> with [threads.platform_chats]."
            exit 0
        fi
        OUT="$(relay_python -c "
from pathlib import Path
import os
from tg_agent_relay.config import load_config
from tg_agent_relay import threads
b = Path('$BRIDGE_DIR')
cfg = load_config(b/'relay.toml', bridge_dir=b)
overlay = Path(os.environ.get('RELAY_CHATS_OVERLAY', b/'.chats.d'/'bindings.json'))
token = os.environ.get('BOT_TOKEN','')
auto = cfg.get('threads', {}).get('auto_create', True)
if auto in (False, 'false', '0', 0):
    row = threads.find_binding(cfg, session='$KV_SESSION', project='$KV_PROJECT', workstream='$KV_WS')
    if not row:
        raise SystemExit('no binding; enable auto_create or /thread bind')
    cid, tid = str(row.get('chat_id','')), str(row.get('thread_id') or '')
    title = threads.build_topic_title('$KV_SESSION', '$KV_PROJECT', '$KV_WS')
    print(f'{cid}|{tid}|{title}|existing')
else:
    cid, tid, title = threads.ensure_topic(
        cfg, token=token, chat_id='$TARGET_CHAT', session='$KV_SESSION',
        project='$KV_PROJECT', workstream='$KV_WS', platform='$KV_PLATFORM',
        handle='$KV_HANDLE', backend='$KV_BACKEND', overlay_path=overlay,
        allowed_chat_id=os.environ.get('ALLOWED_CHAT_ID',''),
    )
    print(f'{cid}|{tid}|{title}|created')
" 2>&1)" || true
        if [[ -z "$OUT" || "$OUT" == *"Error"* || "$OUT" == *"Traceback"* ]]; then
            reply "Ensure failed:
${OUT:-unknown error}"
            exit 0
        fi
        reply "✅ Thread ensured
${OUT}"
        if declare -f load_relay_config >/dev/null 2>&1; then
            load_relay_config "$BRIDGE_DIR/relay.toml"
        fi
        emit_metric "thread" "ensure" "session=$KV_SESSION"
        ;;
    *)
        reply "Unknown /thread subcommand: ${cmd}
Try: list · here · bind · ensure"
        ;;
esac
exit 0
#!/bin/bash
# lib/exec-env.sh — Bridge-root PYTHONPATH for deployed trees (no editable install).
#
# Sourced by lib/python.sh and entry scripts. Safe when BRIDGE_DIR is unset:
# derives bridge root from this file's location.
set -u

_relay_exec_lib="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_relay_exec_root="${BRIDGE_DIR:-$(cd "$_relay_exec_lib/.." && pwd)}"

# tg_agent_relay + providers live under bridge root; lib/*.py modules use lib/ on path.
_relay_py_path="${_relay_exec_root}/lib:${_relay_exec_root}"
if [[ -n "${PYTHONPATH:-}" ]]; then
    case ":${PYTHONPATH}:" in
        *":${_relay_exec_root}:"*) ;;
        *) _relay_py_path="${_relay_py_path}:${PYTHONPATH}" ;;
    esac
fi
export PYTHONPATH="$_relay_py_path"
export BRIDGE_DIR="$_relay_exec_root"

# Optional venv from deploy-local.sh (preferred when present).
if [[ -x "${_relay_exec_root}/.venv/bin/python" ]]; then
    export RELAY_PYTHON="${_relay_exec_root}/.venv/bin/python"
    export PATH="${_relay_exec_root}/.venv/bin:${PATH}"
fi
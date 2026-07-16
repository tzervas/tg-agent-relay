#!/bin/bash
# lib/python_fallback.sh — Python default path with shell recovery.
#
# Sourced by tg-send.sh / tg-poll.sh (do not execute). Shared helpers for:
#   - redacting secrets from error text before stderr/metrics
#   - validating RELAY_PYTHON before exec
#   - sticky failure window (skip re-probe while an outage is ongoing)
#   - bounded import probe
#   - fallback notices + metrics
#
# Environment:
#   RELAY_PYTHON_FALLBACK_TTL     sticky shell duration after failure (default 60, max 3600)
#   RELAY_PYTHON_FALLBACK_QUIET   1 = metrics only (used by the offline suite)
#   RELAY_PYTHON_STICKY=0         always re-probe (higher cost when Python is down)
#   RELAY_PYTHON_STICKY_VERBOSE=1 print sticky hits to stderr (default: metric only)
#   RELAY_PYTHON_PROBE_TIMEOUT    import probe seconds (default 5, max 30)
set -u

# relay_redact_secrets <text>
# Writes a single-line, truncated form of text with common token shapes masked.
# Import and runtime errors can echo credentials; logs should not retain them.
relay_redact_secrets() {
    local s="${1:-}"
    s="$(printf '%s' "$s" | tr '\n\r\t' ' ' | tr -s ' ')"
    s="$(printf '%s' "$s" | sed -E \
        -e 's/BOT_TOKEN[=:][[:space:]]*[A-Za-z0-9:_-]{8,}/BOT_TOKEN=***REDACTED***/g' \
        -e 's/[0-9]{8,12}:[A-Za-z0-9_-]{20,}/TELEGRAM_TOKEN=***REDACTED***/g' \
        -e 's/sk-[A-Za-z0-9_-]{10,}/sk-***REDACTED***/g' \
        -e 's/Bearer[[:space:]]+[A-Za-z0-9._-]{8,}/Bearer ***REDACTED***/g' \
        -e 's/api[_-]?key[=:][[:space:]]*[^[:space:]]+/api_key=***REDACTED***/gi' \
        -e 's/xai-[A-Za-z0-9_-]{10,}/xai-***REDACTED***/g' \
        -e 's/ghp_[A-Za-z0-9]{20,}/ghp_***REDACTED***/g' \
        -e 's/gho_[A-Za-z0-9]{20,}/gho_***REDACTED***/g' \
        -e 's/github_pat_[A-Za-z0-9_]{20,}/github_pat_***REDACTED***/g')"
    if [[ ${#s} -gt 360 ]]; then
        s="${s:0:357}..."
    fi
    printf '%s' "$s"
}

# relay_py_bin_is_safe <path-or-name>
# Returns 0 if the value is acceptable for exec (absolute executable or bare
# python* name on PATH). Rejects shell metacharacters, whitespace, and `..`
# so RELAY_PYTHON cannot be interpreted as a compound command.
relay_py_bin_is_safe() {
    local b="${1:-}"
    [[ -n "$b" ]] || return 1
    case "$b" in
        *[!A-Za-z0-9/_.+-]* | *..* | *' '* | *$'\t'* | *$'\n'* | *';'* | *'|'* | *'&'* | *'$'* | *'`'* | *'('* | *')'* | *"'"* | *'"'*) return 1 ;;
    esac
    if [[ "$b" == /* ]]; then
        [[ -f "$b" && -x "$b" ]] || return 1
        return 0
    fi
    # Relative paths are ambiguous in hooks; only bare interpreter names.
    [[ "$b" != */* ]] || return 1
    [[ "$b" == python* ]] || return 1
    command -v "$b" >/dev/null 2>&1 || return 1
    return 0
}

# relay_py_resolve_safe
# Sets RELAY_PY_BIN to a validated interpreter, or leaves it empty.
# Invalid RELAY_PYTHON overrides are dropped so resolution can continue.
relay_py_resolve_safe() {
    RELAY_PY_BIN=""
    local override_was="${RELAY_PYTHON:-}"
    if [[ -n "${RELAY_PYTHON:-}" ]]; then
        if relay_py_bin_is_safe "$RELAY_PYTHON"; then
            RELAY_PY_BIN="$RELAY_PYTHON"
            return 0
        fi
        RELAY_PYTHON=""
    fi
    if declare -f relay_python_resolve >/dev/null 2>&1; then
        relay_python_resolve || true
    fi
    if [[ -n "${RELAY_PYTHON:-}" ]] && relay_py_bin_is_safe "$RELAY_PYTHON"; then
        RELAY_PY_BIN="$RELAY_PYTHON"
        return 0
    fi
    local cand
    for cand in python3.14 python3.13 python3; do
        if relay_py_bin_is_safe "$cand"; then
            RELAY_PY_BIN="$cand"
            return 0
        fi
    done
    if [[ -n "$override_was" ]]; then
        RELAY_PY_OVERRIDE_REJECTED=1
    else
        RELAY_PY_OVERRIDE_REJECTED=0
    fi
    return 1
}

# relay_py_probe_import <bin> <module>
# Sets RELAY_PY_PROBE_RC / RELAY_PY_PROBE_ERR. Uses `timeout` when available so
# a wedged interpreter does not block the hook indefinitely.
relay_py_probe_import() {
    local bin="$1" mod="$2" t_raw t err rc=0
    RELAY_PY_PROBE_RC=0
    RELAY_PY_PROBE_ERR=""
    t_raw="${RELAY_PYTHON_PROBE_TIMEOUT:-5}"
    [[ "$t_raw" =~ ^[0-9]+$ ]] || t_raw=5
    t="$t_raw"
    if [[ "$t" -lt 1 ]]; then t=1; fi
    if [[ "$t" -gt 30 ]]; then t=30; fi
    if ! relay_py_bin_is_safe "$bin"; then
        RELAY_PY_PROBE_RC=127
        RELAY_PY_PROBE_ERR="interpreter path rejected"
        return 1
    fi
    if command -v timeout >/dev/null 2>&1; then
        err="$(timeout "$t" "$bin" -c "import ${mod}" 2>&1)" || rc=$?
        if [[ "$rc" -eq 124 ]]; then
            err="import probe timed out after ${t}s (module=${mod})"
        fi
    else
        err="$("$bin" -c "import ${mod}" 2>&1)" || rc=$?
    fi
    RELAY_PY_PROBE_RC="$rc"
    RELAY_PY_PROBE_ERR="$err"
    [[ "$rc" -eq 0 ]]
}

# Sticky stamp: BRIDGE_DIR/.python-<kind>-fallback  (kind=send|poll)
# One line: epoch<TAB>reason. Kind is restricted to keep the path under BRIDGE_DIR.
_relay_py_kind_ok() {
    local k="${1:-}"
    [[ "$k" =~ ^[a-z0-9_]{1,16}$ ]]
}

_relay_py_sticky_path() {
    local kind="$1"
    if ! _relay_py_kind_ok "$kind"; then
        printf '%s/.python-invalid-fallback' "${BRIDGE_DIR:-.}"
        return 1
    fi
    printf '%s/.python-%s-fallback' "${BRIDGE_DIR:-.}" "$kind"
}

_relay_py_sticky_ttl() {
    local t="${RELAY_PYTHON_FALLBACK_TTL:-60}"
    [[ "$t" =~ ^[0-9]+$ ]] || t=60
    # Cap so a mis-set env cannot pin shell recovery for days.
    if [[ "$t" -gt 3600 ]]; then
        t=3600
    fi
    printf '%s' "$t"
}

# relay_py_sticky_active <kind>
# Returns 0 and sets RELAY_PY_STICKY_REASON when a recent failure stamp is live.
relay_py_sticky_active() {
    local kind="$1" path age now epoch reason ttl mode last
    RELAY_PY_STICKY_REASON=""
    [[ "${RELAY_PYTHON_STICKY:-1}" == "0" ]] && return 1
    _relay_py_kind_ok "$kind" || return 1
    path="$(_relay_py_sticky_path "$kind")" || return 1
    [[ -f "$path" ]] || return 1
    # Ignore world-writable stamps (untrusted); clear and re-probe.
    mode="$(stat -c '%a' "$path" 2>/dev/null || echo 600)"
    last="${mode: -1}"
    if [[ "$last" =~ ^[0-7]$ ]] && (( last & 2 )); then
        rm -f "$path" 2>/dev/null || true
        return 1
    fi
    now="$(date +%s)"
    IFS=$'\t' read -r epoch reason <"$path" || return 1
    [[ "$epoch" =~ ^[0-9]+$ ]] || return 1
    # Far-future timestamps are not trustworthy; drop them.
    if [[ "$epoch" -gt $((now + 60)) ]]; then
        rm -f "$path" 2>/dev/null || true
        return 1
    fi
    ttl="$(_relay_py_sticky_ttl)"
    age=$((now - epoch))
    if [[ "$age" -ge 0 && "$age" -lt "$ttl" ]]; then
        RELAY_PY_STICKY_REASON="sticky: prior Python failure ${age}s ago (TTL=${ttl}s); ${reason:-unknown}"
        return 0
    fi
    rm -f "$path" 2>/dev/null || true
    return 1
}

# relay_py_sticky_set <kind> <reason>
# Records a failure so subsequent hooks can skip the import probe for a while.
relay_py_sticky_set() {
    local kind="$1" reason="$2" path tmp
    _relay_py_kind_ok "$kind" || return 1
    path="$(_relay_py_sticky_path "$kind")" || return 1
    reason="$(relay_redact_secrets "$reason")"
    tmp="${path}.tmp.$$"
    { printf '%s\t%s\n' "$(date +%s)" "$reason" >"$tmp" && mv -f "$tmp" "$path"; } 2>/dev/null || {
        rm -f "$tmp" 2>/dev/null || true
        return 0
    }
    chmod 600 "$path" 2>/dev/null || true
}

# relay_py_sticky_clear <kind>
# Removes the failure stamp after a successful Python path.
relay_py_sticky_clear() {
    local kind="$1" path
    _relay_py_kind_ok "$kind" || return 0
    path="$(_relay_py_sticky_path "$kind")" || return 0
    rm -f "$path" 2>/dev/null || true
}

# relay_py_announce_fallback <source> <kind> <reason>
# kind: failed | forced | sticky
# source: tg-send | tg-poll (also accepts *.sh names)
# Real failures print recovery context; sticky hits stay metric-only by default
# so high-frequency hooks do not flood stderr with the same outage notice.
relay_py_announce_fallback() {
    local source="$1" kind="$2" reason="$3" env_key
    reason="$(relay_redact_secrets "$reason")"
    case "$source" in
        tg-send|tg-send.sh) env_key=SEND; source=tg-send ;;
        tg-poll|tg-poll.sh) env_key=POLL; source=tg-poll ;;
        *) env_key=SEND ;;
    esac
    if [[ "${RELAY_PYTHON_FALLBACK_QUIET:-0}" != "1" ]]; then
        case "$kind" in
            failed)
                {
                    printf '%s: ERROR — Python default path failed; recovering via shell.\n' "$source"
                    printf '  reason:   %s\n' "$reason"
                    printf '  recovery: continuing with shell %s (allowlist/format/TTS unchanged).\n' "$source"
                    printf '  fix:      deploy tg_agent_relay/ + Python 3.14 (uv sync / RELAY_PYTHON=…);\n'
                    printf '            shell sticks for ~%ss (RELAY_PYTHON_FALLBACK_TTL) then re-probes.\n' "$(_relay_py_sticky_ttl)"
                    printf '            set RELAY_PYTHON_%s=0 only if shell is intentional.\n' "$env_key"
                } >&2
                ;;
            sticky)
                if [[ "${RELAY_PYTHON_STICKY_VERBOSE:-0}" == "1" ]]; then
                    printf '%s: still on shell (sticky failure window) — %s\n' "$source" "$reason" >&2
                fi
                ;;
            forced)
                printf '%s: using shell path (%s)\n' "$source" "$reason" >&2
                ;;
            *)
                printf '%s: Python path skipped (%s) — shell recovery.\n' "$source" "$reason" >&2
                ;;
        esac
    fi
    if declare -f emit_metric >/dev/null 2>&1; then
        emit_metric "$source" "python_fallback" "${kind}: ${reason}"
    fi
}

# relay_py_try_default <kind> <module> [args…]
# kind: send|poll; module: e.g. tg_agent_relay.send
# On success: exec's the module (does not return). On shell path: returns 1 and
# sets RELAY_PY_FALLBACK_KIND / RELAY_PY_FALLBACK_REASON for the caller.
relay_py_try_default() {
    local kind="$1" mod="$2"
    shift 2
    RELAY_PY_FALLBACK_KIND=""
    RELAY_PY_FALLBACK_REASON=""
    RELAY_PY_BIN=""

    if [[ "${kind}" == "send" && "${RELAY_PYTHON_SEND:-1}" == "0" ]]; then
        RELAY_PY_FALLBACK_KIND="forced"
        RELAY_PY_FALLBACK_REASON="RELAY_PYTHON_SEND=0 (explicit shell path)"
        return 1
    fi
    if [[ "${kind}" == "poll" && "${RELAY_PYTHON_POLL:-1}" == "0" ]]; then
        RELAY_PY_FALLBACK_KIND="forced"
        RELAY_PY_FALLBACK_REASON="RELAY_PYTHON_POLL=0 (explicit shell path)"
        return 1
    fi

    # Sticky hit: skip resolve + import for the remainder of the TTL.
    if relay_py_sticky_active "$kind"; then
        RELAY_PY_FALLBACK_KIND="sticky"
        RELAY_PY_FALLBACK_REASON="${RELAY_PY_STICKY_REASON}"
        return 1
    fi

    # shellcheck disable=SC1091
    [[ -f "${BRIDGE_DIR}/lib/exec-env.sh" ]] && source "${BRIDGE_DIR}/lib/exec-env.sh"
    [[ -f "${BRIDGE_DIR}/lib/python.sh" ]] && source "${BRIDGE_DIR}/lib/python.sh"
    if ! relay_py_resolve_safe; then
        RELAY_PY_FALLBACK_KIND="failed"
        if [[ "${RELAY_PY_OVERRIDE_REJECTED:-0}" == "1" ]]; then
            RELAY_PY_FALLBACK_REASON="no usable Python interpreter (RELAY_PYTHON override rejected)"
        else
            RELAY_PY_FALLBACK_REASON="no Python interpreter found (need 3.14 preferred, ≥3.11)"
        fi
        relay_py_sticky_set "$kind" "$RELAY_PY_FALLBACK_REASON"
        return 1
    fi

    if ! relay_py_probe_import "$RELAY_PY_BIN" "$mod"; then
        RELAY_PY_FALLBACK_KIND="failed"
        RELAY_PY_FALLBACK_REASON="import ${mod} failed (rc=${RELAY_PY_PROBE_RC}, interpreter=${RELAY_PY_BIN})"
        if [[ -n "${RELAY_PY_PROBE_ERR:-}" ]]; then
            RELAY_PY_FALLBACK_REASON="${RELAY_PY_FALLBACK_REASON}: $(relay_redact_secrets "$RELAY_PY_PROBE_ERR")"
        fi
        relay_py_sticky_set "$kind" "$RELAY_PY_FALLBACK_REASON"
        return 1
    fi

    relay_py_sticky_clear "$kind"
    exec "$RELAY_PY_BIN" -m "$mod" "$@"
}

#!/bin/bash
# lib/python_fallback.sh — shared adversarial-safe Python→shell recovery.
#
# Sourced by tg-send.sh / tg-poll.sh (never execute). Provides:
#   - secret-aware redaction of error text (no tokens in metrics/stderr)
#   - safe interpreter selection (no shell metacharacters in RELAY_PYTHON)
#   - sticky failure window (avoid re-probing import on every hook under outage)
#   - never-silent recovery for real failures; sticky is metric-only by default
#   - bounded import probe (timeout) so a wedged interpreter cannot hang hooks
#
# Env:
#   RELAY_PYTHON_FALLBACK_TTL     seconds to sticky-shell after failure (default 60, max 3600)
#   RELAY_PYTHON_FALLBACK_QUIET   1 = metric only (tests)
#   RELAY_PYTHON_STICKY=0         disable sticky (always re-probe — costly under outage)
#   RELAY_PYTHON_STICKY_VERBOSE=1 also print sticky hits to stderr (default: metric only)
#   RELAY_PYTHON_PROBE_TIMEOUT    seconds for import probe (default 5, max 30)
#
# Adversarial model (summary):
#   - Hostile RELAY_PYTHON (`; rm -rf`, spaces, `..`) must never reach exec
#   - Import/stderr may echo BOT_TOKEN / sk- / Bearer → redact before log
#   - Hook storms during outage must not fork-probe Python every event
#   - Sticky stamp file is private (0600), kind-validated, atomic write
#   - TTL is capped so a typo cannot pin shell forever
set -u

# relay_redact_secrets <text> → stdout redacted, truncated
relay_redact_secrets() {
    local s="${1:-}"
    # Collapse whitespace / control noise
    s="$(printf '%s' "$s" | tr '\n\r\t' ' ' | tr -s ' ')"
    # Common secret shapes (defense-in-depth; never log raw tokens)
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
    # Cap length (metrics + stderr)
    if [[ ${#s} -gt 360 ]]; then
        s="${s:0:357}..."
    fi
    printf '%s' "$s"
}

# relay_py_bin_is_safe <path-or-name> → 0 if acceptable for exec
# Rejects shell metacharacters / injection in RELAY_PYTHON override.
relay_py_bin_is_safe() {
    local b="${1:-}"
    [[ -n "$b" ]] || return 1
    # No whitespace, no shell metacharacters, no path traversal
    case "$b" in
        *[!A-Za-z0-9/_.+-]* | *..* | *' '* | *$'\t'* | *$'\n'* | *';'* | *'|'* | *'&'* | *'$'* | *'`'* | *'('* | *')'* | *"'"* | *'"'*) return 1 ;;
    esac
    # Must be absolute path or single PATH component name (no relative ./ tricks)
    if [[ "$b" == /* ]]; then
        # Reject non-regular (e.g. unexpected dirs) and require executable
        [[ -f "$b" && -x "$b" ]] || return 1
        return 0
    fi
    # bare name: python3.14, python3, etc. — not paths
    [[ "$b" != */* ]] || return 1
    [[ "$b" == python* ]] || return 1
    command -v "$b" >/dev/null 2>&1 || return 1
    return 0
}

# relay_py_resolve_safe → sets RELAY_PY_BIN (safe) or empty
relay_py_resolve_safe() {
    RELAY_PY_BIN=""
    local override_was="${RELAY_PYTHON:-}"
    if [[ -n "${RELAY_PYTHON:-}" ]]; then
        if relay_py_bin_is_safe "$RELAY_PYTHON"; then
            RELAY_PY_BIN="$RELAY_PYTHON"
            return 0
        fi
        # Hostile/invalid override: do not use it; fall through to resolve
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
    # Preserve signal that override was rejected (callers may include in reason)
    if [[ -n "$override_was" ]]; then
        RELAY_PY_OVERRIDE_REJECTED=1
    else
        RELAY_PY_OVERRIDE_REJECTED=0
    fi
    return 1
}

# Bounded import probe: avoids hung python freezing hooks/monitors.
# relay_py_probe_import <bin> <module> → sets RELAY_PY_PROBE_RC, RELAY_PY_PROBE_ERR
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
        RELAY_PY_PROBE_ERR="unsafe interpreter path rejected"
        return 1
    fi
    # Prefer coreutils timeout when present; else bare probe (still safer than hang forever for most cases)
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
# Line: epoch\treason
# kind must be [a-z0-9_]{1,16} — no path separators
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
    # Cap TTL (adversarial: huge TTL would stick forever)
    if [[ "$t" -gt 3600 ]]; then
        t=3600
    fi
    printf '%s' "$t"
}

# relay_py_sticky_active <kind> → sets RELAY_PY_STICKY_REASON if active
relay_py_sticky_active() {
    local kind="$1" path age now epoch reason ttl
    RELAY_PY_STICKY_REASON=""
    [[ "${RELAY_PYTHON_STICKY:-1}" == "0" ]] && return 1
    _relay_py_kind_ok "$kind" || return 1
    path="$(_relay_py_sticky_path "$kind")" || return 1
    [[ -f "$path" ]] || return 1
    # Refuse world-writable stamps (tamper signal → ignore + clear, re-probe)
    # Last octal digit & 2 ⇒ others-write.
    local mode last
    mode="$(stat -c '%a' "$path" 2>/dev/null || echo 600)"
    last="${mode: -1}"
    if [[ "$last" =~ ^[0-7]$ ]] && (( last & 2 )); then
        rm -f "$path" 2>/dev/null || true
        return 1
    fi
    now="$(date +%s)"
    IFS=$'\t' read -r epoch reason <"$path" || return 1
    [[ "$epoch" =~ ^[0-9]+$ ]] || return 1
    # Future-dated stamp (clock skew / tamper) → clear
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
    # Expired: best-effort clear so next success path stays clean
    rm -f "$path" 2>/dev/null || true
    return 1
}

# relay_py_sticky_set <kind> <reason>
relay_py_sticky_set() {
    local kind="$1" reason="$2" path tmp dir
    _relay_py_kind_ok "$kind" || return 1
    path="$(_relay_py_sticky_path "$kind")" || return 1
    reason="$(relay_redact_secrets "$reason")"
    dir="$(dirname "$path")"
    tmp="${path}.tmp.$$"
    # Atomic write: temp in same dir then rename
    { printf '%s\t%s\n' "$(date +%s)" "$reason" >"$tmp" && mv -f "$tmp" "$path"; } 2>/dev/null || {
        rm -f "$tmp" 2>/dev/null || true
        return 0
    }
    # Best-effort private file (owner read/write only)
    chmod 600 "$path" 2>/dev/null || true
}

# relay_py_sticky_clear <kind>  — call when Python path succeeds
relay_py_sticky_clear() {
    local kind="$1" path
    _relay_py_kind_ok "$kind" || return 0
    path="$(_relay_py_sticky_path "$kind")" || return 0
    rm -f "$path" 2>/dev/null || true
}

# relay_py_announce_fallback <source> <kind> <reason>
# kind: failed | forced | sticky
# source: tg-send | tg-poll  (also accepts tg-send.sh / tg-poll.sh)
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
                    printf '  recovery: continuing with shell %s (same UX contracts: allowlist/format/TTS).\n' "$source"
                    printf '  fix:      deploy tg_agent_relay/ + Python 3.14 (uv sync / safe RELAY_PYTHON=…);\n'
                    printf '            sticky shell ~%ss avoids re-probe storms (RELAY_PYTHON_FALLBACK_TTL).\n' "$(_relay_py_sticky_ttl)"
                    printf '            set RELAY_PYTHON_%s=0 only if shell is intentional.\n' "$env_key"
                } >&2
                ;;
            sticky)
                # Default: quiet on stderr (hooks fire often). Metric still records.
                if [[ "${RELAY_PYTHON_STICKY_VERBOSE:-0}" == "1" ]]; then
                    printf '%s: still on shell (sticky Python failure window) — %s\n' "$source" "$reason" >&2
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

# relay_py_try_exec <kind> <module> [args…]
# kind: send|poll
# module: tg_agent_relay.send | tg_agent_relay.poll
# Returns 0 and execs on success; returns 1 with RELAY_PY_FALLBACK_KIND/REASON set on shell path.
# Caller sources this after BRIDGE_DIR is set; must not have heavy state yet (exec replaces process).
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

    # Dynamic performance: skip resolve+import entirely during sticky window
    if relay_py_sticky_active "$kind"; then
        RELAY_PY_FALLBACK_KIND="sticky"
        RELAY_PY_FALLBACK_REASON="${RELAY_PY_STICKY_REASON}"
        return 1
    fi

    # shellcheck disable=SC1091
    [[ -f "${BRIDGE_DIR}/lib/python.sh" ]] && source "${BRIDGE_DIR}/lib/python.sh"
    if ! relay_py_resolve_safe; then
        RELAY_PY_FALLBACK_KIND="failed"
        if [[ "${RELAY_PY_OVERRIDE_REJECTED:-0}" == "1" ]]; then
            RELAY_PY_FALLBACK_REASON="no safe Python interpreter (RELAY_PYTHON override rejected as unsafe)"
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

    # Success path: clear any prior sticky and exec
    relay_py_sticky_clear "$kind"
    exec "$RELAY_PY_BIN" -m "$mod" "$@"
}

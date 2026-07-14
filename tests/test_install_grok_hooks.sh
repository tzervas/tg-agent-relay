#!/bin/bash
# tests/test_install_grok_hooks.sh - Offline tests for install-grok-hooks.sh.
#
# Covers Claude-parity safety for the Grok installer (issue #61):
#   dry-run plan + no write, default-on Stop write, idempotent no-op,
#   uninstall, malformed refuse, never touches Claude settings.
#
# NO network. Requires jq + python3. Run:
#
#   bash tests/test_install_grok_hooks.sh
#
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL="$REPO_ROOT/install-grok-hooks.sh"

PASS=0
FAIL=0
FAILED_NAMES=()

ok() {
    local name="$1"
    PASS=$((PASS + 1))
    printf 'PASS  %s\n' "$name"
}

fail() {
    local name="$1" detail="${2:-}"
    FAIL=$((FAIL + 1))
    FAILED_NAMES+=("$name")
    printf 'FAIL  %s\n' "$name"
    [[ -n "$detail" ]] && printf '      %s\n' "$detail"
}

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        ok "$name"
    else
        fail "$name" "expected: [$expected]  actual: [$actual]"
    fi
}

if ! command -v jq >/dev/null 2>&1; then
    printf 'test_install_grok_hooks.sh: jq is required\n' >&2
    exit 1
fi
if [[ ! -x "$INSTALL" && ! -f "$INSTALL" ]]; then
    printf 'test_install_grok_hooks.sh: missing %s\n' "$INSTALL" >&2
    exit 1
fi

WORK="$(mktemp -d)"
# shellcheck disable=SC2064
trap 'rm -rf "$WORK"' EXIT

HOOKS="$WORK/hooks/tg-agent-relay.json"
CLAUDE_SETTINGS="$WORK/claude/settings.json"
mkdir -p "$(dirname "$HOOKS")" "$(dirname "$CLAUDE_SETTINGS")"

# Sentinel Claude settings that must never be modified.
printf '%s\n' '{"sentinel":true,"hooks":{"Stop":[{"hooks":[{"type":"command","command":"other"}]}]}}' \
    > "$CLAUDE_SETTINGS"
CLAUDE_BEFORE="$(cksum "$CLAUDE_SETTINGS" | awk '{print $1" "$2}')"

echo "== install-grok-hooks.sh: dry-run / write / no-op / uninstall / malformed =="

# --- dry-run: prints plan + path, writes nothing -----------------------------
OUT="$WORK/dry1.out"
set +e
bash "$INSTALL" --hooks-file "$HOOKS" --dry-run >"$OUT" 2>&1
RC=$?
set -e
assert_eq "dry-run: exit 0" "0" "$RC"
if [[ ! -f "$HOOKS" ]]; then
    ok "dry-run: does not create hooks file"
else
    fail "dry-run: does not create hooks file" "file exists: $HOOKS"
fi
if grep -qF "$HOOKS" "$OUT" && grep -qE 'events enabled:|plan for' "$OUT" && grep -qE 'dry-run' "$OUT"; then
    ok "dry-run: prints target path + planned event set"
else
    fail "dry-run: prints target path + planned event set" "$(cat "$OUT")"
fi
if grep -qE 'Stop' "$OUT"; then
    ok "dry-run: plan mentions default-on Stop"
else
    fail "dry-run: plan mentions default-on Stop" "$(cat "$OUT")"
fi

# --- write: creates file with catalog default-on events (Stop etc) -----------
OUT="$WORK/write1.out"
set +e
bash "$INSTALL" --hooks-file "$HOOKS" >"$OUT" 2>&1
RC=$?
set -e
assert_eq "write: exit 0" "0" "$RC"
if [[ -f "$HOOKS" ]] && jq -e . "$HOOKS" >/dev/null 2>&1; then
    ok "write: creates valid JSON hooks file"
else
    fail "write: creates valid JSON hooks file" "$(cat "$OUT"; cat "$HOOKS" 2>/dev/null)"
fi
if jq -e '.hooks.Stop' "$HOOKS" >/dev/null 2>&1; then
    ok "write: wires Stop (catalog default-on)"
else
    fail "write: wires Stop (catalog default-on)" "$(jq -c . "$HOOKS" 2>/dev/null)"
fi
# Other default-on events from providers/grok
for ev in StopFailure Notification SubagentStop PostToolUseFailure; do
    if jq -e --arg e "$ev" '.hooks[$e]' "$HOOKS" >/dev/null 2>&1; then
        ok "write: wires default-on $ev"
    else
        fail "write: wires default-on $ev" "$(jq -c '.hooks | keys' "$HOOKS" 2>/dev/null)"
    fi
done
# Default-off should stay out unless relay.toml enables it
if jq -e '.hooks.PreToolUse' "$HOOKS" >/dev/null 2>&1; then
    fail "write: does NOT wire PreToolUse (default-off)" "$(jq -c . "$HOOKS")"
else
    ok "write: does NOT wire PreToolUse (default-off)"
fi
# Command points at this bridge's hook-notify-grok.sh
CMD="$(jq -r '.hooks.Stop[0].hooks[0].command' "$HOOKS")"
if [[ "$CMD" == "$REPO_ROOT/hook-notify-grok.sh" ]]; then
    ok "write: Stop command is bridge hook-notify-grok.sh"
else
    fail "write: Stop command is bridge hook-notify-grok.sh" "got: $CMD"
fi

BEFORE_CKSUM="$(cksum "$HOOKS" | awk '{print $1" "$2}')"
BEFORE_MTIME="$(stat -c '%Y' "$HOOKS" 2>/dev/null || stat -f '%m' "$HOOKS")"

# --- second run: no-op, clear message, no unnecessary rewrite ----------------
# Small sleep so mtime would change if rewritten.
sleep 1
OUT="$WORK/noop.out"
set +e
bash "$INSTALL" --hooks-file "$HOOKS" >"$OUT" 2>&1
RC=$?
set -e
assert_eq "no-op: exit 0" "0" "$RC"
if grep -qE 'no changes|already matches' "$OUT"; then
    ok "no-op: reports itself clearly"
else
    fail "no-op: reports itself clearly" "$(cat "$OUT")"
fi
AFTER_CKSUM="$(cksum "$HOOKS" | awk '{print $1" "$2}')"
assert_eq "no-op: file content unchanged (cksum)" "$BEFORE_CKSUM" "$AFTER_CKSUM"
AFTER_MTIME="$(stat -c '%Y' "$HOOKS" 2>/dev/null || stat -f '%m' "$HOOKS")"
assert_eq "no-op: mtime unchanged (no rewrite)" "$BEFORE_MTIME" "$AFTER_MTIME"

# --- dry-run against already-synced file still reports status, no write ------
OUT="$WORK/dry2.out"
set +e
bash "$INSTALL" --hooks-file "$HOOKS" --dry-run >"$OUT" 2>&1
RC=$?
set -e
assert_eq "dry-run synced: exit 0" "0" "$RC"
if grep -qE 'no changes|already matches|dry-run' "$OUT"; then
    ok "dry-run synced: reports status"
else
    fail "dry-run synced: reports status" "$(cat "$OUT")"
fi
assert_eq "dry-run synced: content still unchanged" "$BEFORE_CKSUM" \
    "$(cksum "$HOOKS" | awk '{print $1" "$2}')"

# --- malformed existing file: refuse, exit nonzero, file untouched -----------
BAD="$WORK/hooks/bad.json"
printf '%s\n' 'not valid json {' > "$BAD"
BAD_BEFORE="$(cat "$BAD")"
OUT="$WORK/bad.out"
set +e
bash "$INSTALL" --hooks-file "$BAD" >"$OUT" 2>&1
RC=$?
set -e
if [[ "$RC" -ne 0 ]]; then
    ok "malformed: exits nonzero"
else
    fail "malformed: exits nonzero" "rc=0 output=$(cat "$OUT")"
fi
assert_eq "malformed: file contents untouched" "$BAD_BEFORE" "$(cat "$BAD")"
if grep -qiE 'not valid JSON|refusing' "$OUT"; then
    ok "malformed: message mentions refuse / invalid JSON"
else
    fail "malformed: message mentions refuse / invalid JSON" "$(cat "$OUT")"
fi

# --- uninstall: removes managed file; second uninstall is no-op --------------
OUT="$WORK/uninst.out"
set +e
bash "$INSTALL" --hooks-file "$HOOKS" --uninstall >"$OUT" 2>&1
RC=$?
set -e
assert_eq "uninstall: exit 0" "0" "$RC"
if [[ ! -f "$HOOKS" ]]; then
    ok "uninstall: removes managed hooks file"
else
    fail "uninstall: removes managed hooks file" "still present"
fi

OUT="$WORK/uninst2.out"
set +e
bash "$INSTALL" --hooks-file "$HOOKS" --uninstall >"$OUT" 2>&1
RC=$?
set -e
assert_eq "uninstall missing: exit 0" "0" "$RC"
if grep -qiE 'nothing to uninstall|no managed' "$OUT"; then
    ok "uninstall missing: reports no-op"
else
    fail "uninstall missing: reports no-op" "$(cat "$OUT")"
fi

# --- uninstall --dry-run does not remove -------------------------------------
bash "$INSTALL" --hooks-file "$HOOKS" >/dev/null 2>&1
OUT="$WORK/uninst_dry.out"
set +e
bash "$INSTALL" --hooks-file "$HOOKS" --uninstall --dry-run >"$OUT" 2>&1
RC=$?
set -e
assert_eq "uninstall dry-run: exit 0" "0" "$RC"
if [[ -f "$HOOKS" ]]; then
    ok "uninstall dry-run: leaves file in place"
else
    fail "uninstall dry-run: leaves file in place" "$(cat "$OUT")"
fi
if grep -qE 'dry-run' "$OUT" && grep -qE 'remove' "$OUT"; then
    ok "uninstall dry-run: prints remove plan"
else
    fail "uninstall dry-run: prints remove plan" "$(cat "$OUT")"
fi

# --- never edits Claude settings ---------------------------------------------
CLAUDE_AFTER="$(cksum "$CLAUDE_SETTINGS" | awk '{print $1" "$2}')"
assert_eq "never edits Claude settings.json" "$CLAUDE_BEFORE" "$CLAUDE_AFTER"

# --- catalog-driven: event list comes from provider_catalog ------------------
if command -v python3 >/dev/null 2>&1 || [[ -n "${RELAY_PYTHON:-}" ]]; then
    # shellcheck disable=SC1091
    [[ -f "$REPO_ROOT/lib/python.sh" ]] && source "$REPO_ROOT/lib/python.sh"
    declare -f relay_python >/dev/null 2>&1 || relay_python() { command python3 "$@"; }
    mapfile -t CAT_EVENTS < <(relay_python "$REPO_ROOT/lib/provider_catalog.py" events grok --names-only 2>/dev/null)
    if [[ ${#CAT_EVENTS[@]} -gt 0 ]] && printf '%s\n' "${CAT_EVENTS[@]}" | grep -qx 'Stop'; then
        ok "catalog: provider_catalog.py events grok includes Stop"
    else
        fail "catalog: provider_catalog.py events grok includes Stop" "events=${CAT_EVENTS[*]:-}"
    fi
else
    ok "catalog: provider_catalog check skipped (no python)"
fi

# --- empty --hooks-file rejected ---------------------------------------------
set +e
bash "$INSTALL" --hooks-file "" >"$WORK/empty_path.out" 2>&1
RC=$?
set -e
if [[ "$RC" -ne 0 ]]; then
    ok "empty --hooks-file: rejected nonzero"
else
    fail "empty --hooks-file: rejected nonzero" "$(cat "$WORK/empty_path.out")"
fi

echo
echo "=============================="
echo "Results: $PASS passed, $FAIL failed"
if (( FAIL > 0 )); then
    printf 'Failed:\n'
    for n in "${FAILED_NAMES[@]}"; do
        printf '  - %s\n' "$n"
    done
    exit 1
fi
exit 0

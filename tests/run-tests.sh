#!/bin/bash
# tests/run-tests.sh - Offline unit tests for TG Agent Relay.
#
# NO network calls. Every test that would otherwise hit Telegram swaps in a
# mock tg-send.sh that records its message to a file instead of curling out
# (see setup_temp_bridge below). Run:
#
#   bash tests/run-tests.sh
#
# Exits 0 iff every check passes (bash -n, shellcheck, and every assertion
# below); prints a PASS/FAIL line per check and a summary - never silent
# about a failure.
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

assert_empty() {
    local name="$1" actual="$2"
    if [[ -z "$actual" ]]; then
        ok "$name"
    else
        fail "$name" "expected empty, got: [$actual]"
    fi
}

# --- setup_temp_bridge -----------------------------------------------------
# A throwaway directory that SYMLINKS every real script/lib (so tests always
# run against current source, never a stale copy) except tg-send.sh, which
# is replaced with a mock that records its message to .recorded instead of
# hitting the network. Callers write/omit their own relay.toml into the
# returned dir per-test.
setup_temp_bridge() {
    local dir
    dir="$(mktemp -d)"
    ln -s "$REPO_ROOT/hook-notify.sh" "$dir/hook-notify.sh"
    ln -s "$REPO_ROOT/relay-notify.sh" "$dir/relay-notify.sh"
    mkdir -p "$dir/adapters" "$dir/lib" "$dir/handlers"
    ln -s "$REPO_ROOT/adapters/claude-code.sh" "$dir/adapters/claude-code.sh"
    ln -s "$REPO_ROOT/lib/relay-config.sh" "$dir/lib/relay-config.sh"
    ln -s "$REPO_ROOT/lib/relay-common.sh" "$dir/lib/relay-common.sh"
    ln -s "$REPO_ROOT/lib/toml_to_json.py" "$dir/lib/toml_to_json.py"
    ln -s "$REPO_ROOT/handlers/example-echo.sh" "$dir/handlers/example-echo.sh"

    cat > "$dir/tg-send.sh" <<'MOCK'
#!/bin/bash
set -u
if [[ $# -gt 0 ]]; then MSG="$*"; else MSG="$(cat)"; fi
d="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
printf '%s' "$MSG" > "$d/.recorded"
exit 0
MOCK
    chmod +x "$dir/tg-send.sh"
    printf '%s' "$dir"
}

recorded() {
    local dir="$1"
    [[ -f "$dir/.recorded" ]] && cat "$dir/.recorded" || printf ''
}

clear_recorded() {
    rm -f "$1/.recorded"
}

# ============================================================================
echo "== bash -n (syntax) =="
for f in tg-send.sh tg-poll.sh hook-notify.sh relay-notify.sh go-live.sh \
         watch-go-live.sh adapters/claude-code.sh adapters/generic-example.sh \
         lib/relay-common.sh lib/relay-config.sh; do
    if bash -n "$REPO_ROOT/$f" 2>/tmp/synerr; then
        ok "syntax: $f"
    else
        fail "syntax: $f" "$(cat /tmp/synerr)"
    fi
done

echo "== shellcheck =="
if command -v shellcheck >/dev/null 2>&1; then
    for f in tg-send.sh tg-poll.sh hook-notify.sh relay-notify.sh \
             adapters/claude-code.sh adapters/generic-example.sh \
             lib/relay-common.sh lib/relay-config.sh; do
        if out="$(shellcheck "$REPO_ROOT/$f" 2>&1)"; then
            ok "shellcheck: $f"
        else
            fail "shellcheck: $f" "$out"
        fi
    done
else
    printf 'SKIP  shellcheck not installed - skipping (never-silent: this line IS the record)\n'
fi

# ============================================================================
echo "== adapters/claude-code.sh: backward-compat with the OLD hook-notify.sh =="
# Golden strings computed by hand from the ORIGINAL (pre-refactor)
# hook-notify.sh logic (git history: the version this repo shipped before
# the generic-core/adapter split). If these ever drift from what the
# adapter actually produces, backward-compat broke.
BRIDGE1="$(setup_temp_bridge)"

PAYLOAD_SUBAGENT='{"hook_event_name":"SubagentStop","agent_type":"general-purpose","last_assistant_message":"Task done.\nAll green."}'
printf '%s' "$PAYLOAD_SUBAGENT" | "$BRIDGE1/hook-notify.sh" >/dev/null 2>&1
assert_eq "SubagentStop -> unchanged summary text" \
    "✅ general-purpose finished — Task done. All green." \
    "$(recorded "$BRIDGE1")"
clear_recorded "$BRIDGE1"

PAYLOAD_NOTIF='{"hook_event_name":"Notification","notification_type":"idle_prompt","message":"Waiting for your input"}'
printf '%s' "$PAYLOAD_NOTIF" | "$BRIDGE1/hook-notify.sh" >/dev/null 2>&1
assert_eq "Notification -> unchanged summary text" \
    "🔔 idle_prompt: Waiting for your input" \
    "$(recorded "$BRIDGE1")"
clear_recorded "$BRIDGE1"

PAYLOAD_UNKNOWN='{"hook_event_name":"WeirdFutureEvent"}'
printf '%s' "$PAYLOAD_UNKNOWN" | "$BRIDGE1/hook-notify.sh" >/dev/null 2>&1
assert_eq "unknown event -> unchanged fallback text" \
    "ℹ️ Claude Code event: WeirdFutureEvent" \
    "$(recorded "$BRIDGE1")"
clear_recorded "$BRIDGE1"

# No stdin payload at all -> silent no-op (unchanged contract).
printf '' | "$BRIDGE1/hook-notify.sh" >/dev/null 2>&1
assert_empty "empty payload -> no send" "$(recorded "$BRIDGE1")"

rm -rf "$BRIDGE1"

echo "== adapters/claude-code.sh: relay.toml overrides (enabled=false, prefix) =="
BRIDGE2="$(setup_temp_bridge)"
cp "$REPO_ROOT/tests/fixtures/relay.toml" "$BRIDGE2/relay.toml"

printf '%s' "$PAYLOAD_SUBAGENT" | "$BRIDGE2/hook-notify.sh" >/dev/null 2>&1
assert_empty "SubagentStop disabled via relay.toml -> no send" "$(recorded "$BRIDGE2")"
clear_recorded "$BRIDGE2"

printf '%s' "$PAYLOAD_NOTIF" | "$BRIDGE2/hook-notify.sh" >/dev/null 2>&1
assert_eq "Notification prefix overridden via relay.toml" \
    "⭐ idle_prompt: Waiting for your input" \
    "$(recorded "$BRIDGE2")"
clear_recorded "$BRIDGE2"

rm -rf "$BRIDGE2"

# ============================================================================
echo "== relay-notify.sh: generic harness-agnostic entry point =="
BRIDGE3="$(setup_temp_bridge)"

"$BRIDGE3/relay-notify.sh" "hello world" >/dev/null 2>&1
assert_eq "raw text (args, no relay.toml)" "hello world" "$(recorded "$BRIDGE3")"
clear_recorded "$BRIDGE3"

printf 'hi there' | "$BRIDGE3/relay-notify.sh" >/dev/null 2>&1
assert_eq "raw text (stdin, no relay.toml)" "hi there" "$(recorded "$BRIDGE3")"
clear_recorded "$BRIDGE3"

printf '{"label":"deploy","text":"finished OK"}' | "$BRIDGE3/relay-notify.sh" >/dev/null 2>&1
assert_eq "structured JSON stdin" "deploy: finished OK" "$(recorded "$BRIDGE3")"
clear_recorded "$BRIDGE3"

"$BRIDGE3/relay-notify.sh" --label deploy "finished OK" >/dev/null 2>&1
assert_eq "structured --label args" "deploy: finished OK" "$(recorded "$BRIDGE3")"
clear_recorded "$BRIDGE3"

"$BRIDGE3/relay-notify.sh" --raw '{"label":"not-really-json-mode"}' >/dev/null 2>&1
assert_eq "--raw bypasses JSON-sniff entirely" \
    '{"label":"not-really-json-mode"}' \
    "$(recorded "$BRIDGE3")"
clear_recorded "$BRIDGE3"

"$BRIDGE3/relay-notify.sh" >/dev/null 2>&1 </dev/null
assert_empty "no input at all -> no send" "$(recorded "$BRIDGE3")"

rm -rf "$BRIDGE3"

echo "== relay-notify.sh: [generic].prefix from relay.toml =="
BRIDGE4="$(setup_temp_bridge)"
cp "$REPO_ROOT/tests/fixtures/relay.toml" "$BRIDGE4/relay.toml"

"$BRIDGE4/relay-notify.sh" "hello" >/dev/null 2>&1
assert_eq "generic prefix applied (non-raw)" "📣 hello" "$(recorded "$BRIDGE4")"
clear_recorded "$BRIDGE4"

"$BRIDGE4/relay-notify.sh" --raw "hello" >/dev/null 2>&1
assert_eq "generic prefix NOT applied under --raw" "hello" "$(recorded "$BRIDGE4")"
clear_recorded "$BRIDGE4"

rm -rf "$BRIDGE4"

# ============================================================================
echo "== tg-poll.sh: classify_command() (in-chat commands) =="
# Source tg-poll.sh to get its functions WITHOUT starting the infinite poll
# loop (see the file's own BASH_SOURCE-vs-\$0 guard at the bottom).
# shellcheck disable=SC1091
source "$REPO_ROOT/tg-poll.sh"

RELAY_CONFIG_JSON="{}"
assert_empty "no relay.toml -> never tags (plain text)" "$(classify_command "status update")"
assert_empty "no relay.toml -> never tags (slash-looking text)" "$(classify_command "/status")"

RELAY_CONFIG_JSON='{"commands":{"status":{"keyword":"status","slash":"/status","tag":"status"},"pause":{"keyword":"pause","slash":"/pause"}}}'
assert_eq "exact slash match" "status" "$(classify_command "/status")"
assert_eq "slash with args" "status" "$(classify_command "/status now please")"
assert_eq "keyword with space" "status" "$(classify_command "status update please")"
assert_eq "keyword with colon" "status" "$(classify_command "status: how's it going?")"
assert_eq "second command, default tag = table name" "pause" "$(classify_command "pause the deployment")"
assert_empty "no match on unrelated text" "$(classify_command "just chatting here")"
assert_empty "slash prefix boundary: /statusfoo must NOT match /status" "$(classify_command "/statusfoo")"
assert_empty "keyword prefix boundary: statusfoo must NOT match status" "$(classify_command "statusfoo bar")"

# ============================================================================
echo "== tg-poll.sh: command_field() / dispatch_command() (forward vs relay-handled seam) =="
RELAY_CONFIG_JSON='{"commands":{"helpme":{"keyword":"help","tag":"assist"},"example":{"keyword":"example","slash":"/example","mode":"relay","handler":"handlers/example-echo.sh"}}}'

assert_eq "command_field falls back to its default when the field is absent" \
    "forward" "$(command_field helpme mode forward)"
assert_eq "command_field returns the configured tag (name != tag)" \
    "assist" "$(command_field helpme tag helpme)"

assert_eq "dispatch_command (forward, default mode) emits the CONFIGURED tag, not the table name" \
    "[telegram:cmd:assist] help me out" \
    "$(dispatch_command helpme "help me out")"

# mode = "relay": dispatch to a real handler script, using a throwaway
# bridge dir so the handler's own $BRIDGE_DIR resolves there (not this
# repo checkout) - proves the routing seam end-to-end, offline.
BRIDGE6="$(setup_temp_bridge)"
OLD_BRIDGE_DIR="$BRIDGE_DIR"
BRIDGE_DIR="$BRIDGE6"
RELAY_DISPATCH_OUT="$(dispatch_command example "example payload text")"
# The handler runs detached (&) - give it a moment to write its marker.
for _ in 1 2 3 4 5; do
    [[ -f "$BRIDGE6/.example-echo-received" ]] && break
    sleep 0.2
done
BRIDGE_DIR="$OLD_BRIDGE_DIR"
assert_empty "relay-handled command emits NOTHING to the agent (zero model tokens)" "$RELAY_DISPATCH_OUT"
assert_eq "relay-handled command's handler actually ran" \
    "example payload text" \
    "$(cat "$BRIDGE6/.example-echo-received" 2>/dev/null || true)"
rm -rf "$BRIDGE6"

# ============================================================================
echo "== lib/relay-common.sh: emit_metric() (metrics event hook point) =="
TMPLOG="$(mktemp -u)"
RELAY_METRICS_LOG="$TMPLOG" emit_metric "test-source" "test-event" "detail-here"
if [[ -f "$TMPLOG" ]] && grep -qF $'\ttest-source\ttest-event\tdetail-here' "$TMPLOG"; then
    ok "emit_metric appends a TSV line (epoch/source/event/detail)"
else
    fail "emit_metric appends a TSV line" "$(cat "$TMPLOG" 2>/dev/null || echo '<no file written>')"
fi
rm -f "$TMPLOG"

# Never-blocking/never-fatal: an unwritable log path must not error or
# exit the caller (this is a `set -u` script - reaching the next line at
# all proves emit_metric degraded gracefully).
RELAY_METRICS_LOG="/nonexistent-dir-does-not-exist/metrics.log" emit_metric "test" "test" ""
ok "emit_metric never fails/exits on an unwritable log path"

# ============================================================================
echo "== lib/relay-config.sh: cfg_get / cfg_has_section =="
RELAY_CONFIG_JSON="{}"
assert_eq "cfg_get default with no config" "3500" "$(cfg_get '.general.page_size' 3500)"

RELAY_CONFIG_JSON='{"general":{"page_size":9999}}'
assert_eq "cfg_get override present" "9999" "$(cfg_get '.general.page_size' 3500)"

RELAY_CONFIG_JSON='{"claude_code":{"SubagentStop":{"enabled":false}}}'
assert_eq "cfg_get honors an explicit false (not treated as absent)" \
    "false" "$(cfg_get '.claude_code."SubagentStop".enabled' true)"

RELAY_CONFIG_JSON="{}"
assert_eq "cfg_has_section false with no config" "no" "$(cfg_has_section commands && echo yes || echo no)"
RELAY_CONFIG_JSON='{"commands":{"status":{"keyword":"status"}}}'
assert_eq "cfg_has_section true when configured" "yes" "$(cfg_has_section commands && echo yes || echo no)"

echo "== lib/relay-config.sh: full relay.toml -> JSON pipeline (real file, real python3) =="
load_relay_config "$REPO_ROOT/tests/fixtures/relay.toml"
assert_eq "page_size from real relay.toml" "1234" "$(cfg_get '.general.page_size' 3500)"
assert_eq "generic prefix from real relay.toml" "📣" "$(cfg_get '.generic.prefix' '')"
assert_eq "SubagentStop enabled=false from real relay.toml" "false" "$(cfg_get '.claude_code."SubagentStop".enabled' true)"

load_relay_config "$REPO_ROOT/relay.toml.does.not.exist"
assert_eq "load_relay_config with a missing file -> empty config" "{}" "$RELAY_CONFIG_JSON"
assert_eq "cfg_get still returns the default after a missing-file load" "3500" "$(cfg_get '.general.page_size' 3500)"

# ============================================================================
echo "== tg-send.sh: config-fallback (no relay.toml -> unchanged behavior) =="
BRIDGE5="$(setup_temp_bridge)"
# tg-send.sh itself (not the mock) - exercise its real no-token no-op path
# and its real config-fallback PAGE_SIZE/PAGE_DELAY resolution.
ln -sf "$REPO_ROOT/tg-send.sh" "$BRIDGE5/tg-send.sh"
"$BRIDGE5/tg-send.sh" "hello, nobody is listening" >/dev/null 2>&1
if [[ ! -f "$BRIDGE5/.last-sent" ]]; then
    ok "tg-send.sh with no .env -> silent no-op (unchanged)"
else
    fail "tg-send.sh with no .env -> silent no-op (unchanged)" "a .last-sent file was written"
fi
rm -rf "$BRIDGE5"

# ============================================================================
echo
echo "============================================================"
printf 'Total: %d   Pass: %d   Fail: %d\n' "$((PASS + FAIL))" "$PASS" "$FAIL"
if (( FAIL > 0 )); then
    printf 'FAILED: %s\n' "${FAILED_NAMES[*]}"
    exit 1
fi
exit 0

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
    ln -s "$REPO_ROOT/install-hooks.sh" "$dir/install-hooks.sh"
    mkdir -p "$dir/adapters" "$dir/lib" "$dir/handlers"
    ln -s "$REPO_ROOT/adapters/claude-code.sh" "$dir/adapters/claude-code.sh"
    ln -s "$REPO_ROOT/lib/relay-config.sh" "$dir/lib/relay-config.sh"
    ln -s "$REPO_ROOT/lib/relay-common.sh" "$dir/lib/relay-common.sh"
    ln -s "$REPO_ROOT/lib/claude-code-events.sh" "$dir/lib/claude-code-events.sh"
    ln -s "$REPO_ROOT/lib/toml_to_json.py" "$dir/lib/toml_to_json.py"
    ln -s "$REPO_ROOT/lib/metrics_agg.py" "$dir/lib/metrics_agg.py"
    ln -s "$REPO_ROOT/lib/dashboard_render.py" "$dir/lib/dashboard_render.py"
    ln -s "$REPO_ROOT/handlers/example-echo.sh" "$dir/handlers/example-echo.sh"
    ln -s "$REPO_ROOT/handlers/dashboard.sh" "$dir/handlers/dashboard.sh"
    ln -s "$REPO_ROOT/handlers/stats.sh" "$dir/handlers/stats.sh"
    ln -s "$REPO_ROOT/handlers/uptime.sh" "$dir/handlers/uptime.sh"
    ln -s "$REPO_ROOT/handlers/help.sh" "$dir/handlers/help.sh"

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
         watch-go-live.sh install-hooks.sh \
         adapters/claude-code.sh adapters/generic-example.sh \
         lib/relay-common.sh lib/relay-config.sh lib/claude-code-events.sh lib/tts.sh \
         handlers/dashboard.sh handlers/stats.sh handlers/uptime.sh handlers/help.sh; do
    if bash -n "$REPO_ROOT/$f" 2>/tmp/synerr; then
        ok "syntax: $f"
    else
        fail "syntax: $f" "$(cat /tmp/synerr)"
    fi
done

echo "== shellcheck =="
if command -v shellcheck >/dev/null 2>&1; then
    for f in tg-send.sh tg-poll.sh hook-notify.sh relay-notify.sh install-hooks.sh \
             adapters/claude-code.sh adapters/generic-example.sh \
             lib/relay-common.sh lib/relay-config.sh lib/claude-code-events.sh lib/tts.sh \
             handlers/dashboard.sh handlers/stats.sh handlers/uptime.sh handlers/help.sh; do
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
echo "== lib/relay-common.sh: render_template() (shared {placeholder} engine) =="
# shellcheck disable=SC1091
source "$REPO_ROOT/lib/relay-common.sh"

assert_eq "render_template: no template text, no placeholders" \
    "hello world" "$(render_template "hello world")"
assert_eq "render_template: single placeholder" \
    "hello, Bob" "$(render_template "hello, {name}" name "Bob")"
assert_eq "render_template: repeated placeholder substituted everywhere" \
    "Bob said hi to Bob" "$(render_template "{name} said hi to {name}" name "Bob")"
assert_eq "render_template: multiple distinct placeholders" \
    "[1/3] page one" "$(render_template "[{k}/{n}] {label}" k 1 n 3 label "page one")"
assert_eq "render_template: unknown placeholder left LITERAL (never silently blanked)" \
    "prefix: {typo}" "$(render_template "prefix: {typo}" prefix "X")"
assert_eq "render_template: empty value substitutes to nothing (not the literal braces)" \
    "got: " "$(render_template "got: {x}" x "")"

# ============================================================================
echo "== adapters/claude-code.sh: [claude_code.<Event>].format templates =="
BRIDGE_FMT="$(setup_temp_bridge)"
cat > "$BRIDGE_FMT/relay.toml" <<'TOML'
[claude_code.SubagentStop]
format = "AGENT={agent} MSG={message}"

[claude_code.SessionStart]
enabled = true
format = "session up, source={source}"

[claude_code.PreToolUse]
enabled = true
TOML

printf '%s' "$PAYLOAD_SUBAGENT" | "$BRIDGE_FMT/hook-notify.sh" >/dev/null 2>&1
assert_eq "SubagentStop: custom format overrides the built-in default text" \
    "AGENT=general-purpose MSG=Task done. All green." \
    "$(recorded "$BRIDGE_FMT")"
clear_recorded "$BRIDGE_FMT"

PAYLOAD_SESSION_START='{"hook_event_name":"SessionStart","source":"resume"}'
printf '%s' "$PAYLOAD_SESSION_START" | "$BRIDGE_FMT/hook-notify.sh" >/dev/null 2>&1
assert_eq "SessionStart: enabled=true (new default is false) + custom format both honored" \
    "session up, source=resume" \
    "$(recorded "$BRIDGE_FMT")"
clear_recorded "$BRIDGE_FMT"

PAYLOAD_PRETOOL='{"hook_event_name":"PreToolUse","tool_name":"Bash"}'
printf '%s' "$PAYLOAD_PRETOOL" | "$BRIDGE_FMT/hook-notify.sh" >/dev/null 2>&1
assert_eq "PreToolUse: enabled=true (new default false), no format -> built-in default text" \
    "🔧 using Bash" \
    "$(recorded "$BRIDGE_FMT")"
clear_recorded "$BRIDGE_FMT"

rm -rf "$BRIDGE_FMT"

echo "== adapters/claude-code.sh: new per-event default-enabled table =="
BRIDGE_DEF="$(setup_temp_bridge)"
# No relay.toml at all: PreToolUse (one of the 25 newly-explicit, opt-in
# events) must NOT fire, but a genuinely unrecognized event must still fire
# (the preserved universal default - see lib/claude-code-events.sh).
PAYLOAD_PRETOOL='{"hook_event_name":"PreToolUse","tool_name":"Bash"}'
printf '%s' "$PAYLOAD_PRETOOL" | "$BRIDGE_DEF/hook-notify.sh" >/dev/null 2>&1
assert_empty "PreToolUse with no relay.toml -> opt-in default, no send" "$(recorded "$BRIDGE_DEF")"
clear_recorded "$BRIDGE_DEF"

PAYLOAD_FUTURE='{"hook_event_name":"SomeBrandNewEvent"}'
printf '%s' "$PAYLOAD_FUTURE" | "$BRIDGE_DEF/hook-notify.sh" >/dev/null 2>&1
assert_eq "an unrecognized/future event with no relay.toml -> still fires (preserved universal default)" \
    "ℹ️ Claude Code event: SomeBrandNewEvent" \
    "$(recorded "$BRIDGE_DEF")"
clear_recorded "$BRIDGE_DEF"

rm -rf "$BRIDGE_DEF"

# ============================================================================
echo "== relay-notify.sh: [generic].format template =="
BRIDGE_GFMT="$(setup_temp_bridge)"
cat > "$BRIDGE_GFMT/relay.toml" <<'TOML'
[generic]
prefix = "📣"
format = "<<{prefix}>> {label} :: {text}"
TOML

"$BRIDGE_GFMT/relay-notify.sh" --label deploy "finished OK" >/dev/null 2>&1
assert_eq "[generic].format overrides the built-in structured shape" \
    "<<📣>> deploy :: finished OK" \
    "$(recorded "$BRIDGE_GFMT")"
clear_recorded "$BRIDGE_GFMT"

"$BRIDGE_GFMT/relay-notify.sh" --raw "untouched" >/dev/null 2>&1
assert_eq "[generic].format has no effect under --raw" \
    "untouched" \
    "$(recorded "$BRIDGE_GFMT")"
clear_recorded "$BRIDGE_GFMT"

rm -rf "$BRIDGE_GFMT"

# ============================================================================
echo "== install-hooks.sh: idempotent, merge-not-clobber settings.json sync =="
BRIDGE_IH="$(setup_temp_bridge)"
cp "$REPO_ROOT/tests/fixtures/relay.toml" "$BRIDGE_IH/relay.toml"
SETTINGS_FIXTURE="$(mktemp -u)"
cp "$REPO_ROOT/tests/fixtures/settings-with-other-hooks.json" "$SETTINGS_FIXTURE"

"$BRIDGE_IH/install-hooks.sh" --settings "$SETTINGS_FIXTURE" >/tmp/install_hooks_out 2>&1
INSTALL_RC=$?
if [[ $INSTALL_RC -eq 0 ]] && jq -e . "$SETTINGS_FIXTURE" >/dev/null 2>&1; then
    ok "install-hooks.sh: exits 0 and leaves valid JSON behind"
else
    fail "install-hooks.sh: exits 0 and leaves valid JSON behind" "$(cat /tmp/install_hooks_out)"
fi

assert_eq "install-hooks.sh: preserves a pre-existing unrelated key" \
    "auto" "$(jq -r '.permissions.defaultMode' "$SETTINGS_FIXTURE")"
assert_eq "install-hooks.sh: preserves another tool's hook on the same event" \
    "/opt/other-tool/notify.sh" \
    "$(jq -r '.hooks.SubagentStop[] | select(.hooks[0].command == "/opt/other-tool/notify.sh") | .hooks[0].command' "$SETTINGS_FIXTURE")"
assert_eq "install-hooks.sh: wires Notification (default-enabled, only its prefix overridden) alongside the other tool" \
    "$BRIDGE_IH/hook-notify.sh" \
    "$(jq -r --arg cmd "$BRIDGE_IH/hook-notify.sh" '.hooks.Notification[] | select(.hooks[0].command == $cmd) | .hooks[0].command' "$SETTINGS_FIXTURE")"
assert_empty "install-hooks.sh: does NOT wire SubagentStop (enabled=false via the fixture relay.toml)" \
    "$(jq -r '.hooks.SubagentStop // [] | map(select(.hooks[0].command | test("hook-notify"))) | .[]' "$SETTINGS_FIXTURE" 2>/dev/null)"

# Re-run with no relay.toml change -> byte-identical, reported as a no-op.
BEFORE_HASH="$(jq -S . "$SETTINGS_FIXTURE")"
"$BRIDGE_IH/install-hooks.sh" --settings "$SETTINGS_FIXTURE" >/tmp/install_hooks_out2 2>&1
AFTER_HASH="$(jq -S . "$SETTINGS_FIXTURE")"
assert_eq "install-hooks.sh: re-run with unchanged relay.toml is idempotent (no diff)" \
    "$BEFORE_HASH" "$AFTER_HASH"
if grep -q "no changes" /tmp/install_hooks_out2; then
    ok "install-hooks.sh: idempotent re-run reports itself as a no-op (never-silent)"
else
    fail "install-hooks.sh: idempotent re-run reports itself as a no-op (never-silent)" "$(cat /tmp/install_hooks_out2)"
fi

# --uninstall removes only our entries, leaves the other tool's alone.
"$BRIDGE_IH/install-hooks.sh" --settings "$SETTINGS_FIXTURE" --uninstall >/dev/null 2>&1
assert_empty "install-hooks.sh --uninstall: removes our Notification entry" \
    "$(jq -r --arg cmd "$BRIDGE_IH/hook-notify.sh" '.hooks.Notification // [] | map(select(.hooks[0].command == $cmd)) | .[]' "$SETTINGS_FIXTURE" 2>/dev/null)"
assert_eq "install-hooks.sh --uninstall: the OTHER tool's SubagentStop hook survives (untouched the whole time)" \
    "/opt/other-tool/notify.sh" \
    "$(jq -r '.hooks.SubagentStop[0].hooks[0].command' "$SETTINGS_FIXTURE")"
assert_eq "install-hooks.sh --uninstall: unrelated top-level keys still intact" \
    "dark" "$(jq -r '.theme' "$SETTINGS_FIXTURE")"

# --dry-run never writes.
cp "$REPO_ROOT/tests/fixtures/settings-with-other-hooks.json" "$SETTINGS_FIXTURE"
BEFORE_DRY="$(jq -S . "$SETTINGS_FIXTURE")"
"$BRIDGE_IH/install-hooks.sh" --settings "$SETTINGS_FIXTURE" --dry-run >/dev/null 2>&1
AFTER_DRY="$(jq -S . "$SETTINGS_FIXTURE")"
assert_eq "install-hooks.sh --dry-run: never writes to settings.json" \
    "$BEFORE_DRY" "$AFTER_DRY"

# A missing settings.json is created fresh (mkdir -p + write), not an
# error. Checks .hooks.StopFailure (default-enabled, untouched by the
# BRIDGE_IH fixture relay.toml which only overrides SubagentStop/Notification).
NO_SETTINGS_DIR="$(mktemp -d)"
"$BRIDGE_IH/install-hooks.sh" --settings "$NO_SETTINGS_DIR/nested/settings.json" >/dev/null 2>&1
if jq -e '.hooks.StopFailure' "$NO_SETTINGS_DIR/nested/settings.json" >/dev/null 2>&1; then
    ok "install-hooks.sh: creates a missing settings.json (with parent dirs) from scratch"
else
    fail "install-hooks.sh: creates a missing settings.json (with parent dirs) from scratch" "no file / no hooks written"
fi
rm -rf "$NO_SETTINGS_DIR"

# A malformed settings.json is refused, never guessed at / overwritten.
BAD_SETTINGS="$(mktemp)"
printf '{not valid json' > "$BAD_SETTINGS"
BAD_BEFORE="$(cat "$BAD_SETTINGS")"
"$BRIDGE_IH/install-hooks.sh" --settings "$BAD_SETTINGS" >/tmp/install_hooks_bad 2>&1
BAD_RC=$?
BAD_AFTER="$(cat "$BAD_SETTINGS")"
if [[ $BAD_RC -ne 0 && "$BAD_BEFORE" == "$BAD_AFTER" ]]; then
    ok "install-hooks.sh: refuses to touch a malformed settings.json (exits nonzero, file untouched)"
else
    fail "install-hooks.sh: refuses to touch a malformed settings.json" "rc=$BAD_RC before=[$BAD_BEFORE] after=[$BAD_AFTER]"
fi
rm -f "$BAD_SETTINGS"

rm -f "$SETTINGS_FIXTURE" /tmp/install_hooks_out /tmp/install_hooks_out2 /tmp/install_hooks_bad
rm -rf "$BRIDGE_IH"

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
echo "== tg-poll.sh: dispatch_command() routes the four dashboard commands (relay.toml.example shape) =="
RELAY_CONFIG_JSON='{"commands":{"dashboard":{"keyword":"dashboard","slash":"/dashboard","mode":"relay","handler":"handlers/dashboard.sh"},"stats":{"keyword":"stats","slash":"/stats","mode":"relay","handler":"handlers/stats.sh"},"uptime":{"keyword":"uptime","slash":"/uptime","mode":"relay","handler":"handlers/uptime.sh"},"help":{"keyword":"help","slash":"/help","mode":"relay","handler":"handlers/help.sh"}}}'
assert_eq "classify_command matches /dashboard" "dashboard" "$(classify_command "/dashboard")"
assert_eq "classify_command matches /stats" "stats" "$(classify_command "/stats")"
assert_eq "classify_command matches /uptime" "uptime" "$(classify_command "/uptime")"
assert_eq "classify_command matches /help" "help" "$(classify_command "/help")"
assert_eq "command_field: dashboard mode is relay" "relay" "$(command_field dashboard mode forward)"
assert_eq "command_field: dashboard handler path" "handlers/dashboard.sh" "$(command_field dashboard handler '')"

# ============================================================================
echo "== handlers/stats.sh: relay-handled command runs end-to-end, zero model tokens =="
BRIDGE7="$(setup_temp_bridge)"
cp "$REPO_ROOT/tests/fixtures/metrics-synthetic.log" "$BRIDGE7/.metrics.log"
"$BRIDGE7/handlers/stats.sh" "/stats" >/dev/null 2>&1
STATS_OUT="$(recorded "$BRIDGE7")"
if [[ "$STATS_OUT" == *"Relay stats"* && "$STATS_OUT" == *"model-turns avoided"* ]]; then
    ok "handlers/stats.sh sends a real stats reply via relay-notify.sh -> tg-send.sh"
else
    fail "handlers/stats.sh sends a real stats reply via relay-notify.sh -> tg-send.sh" "$STATS_OUT"
fi
rm -rf "$BRIDGE7"

echo "== handlers/dashboard.sh: never fails to send something (image OR text) =="
BRIDGE8="$(setup_temp_bridge)"
cp "$REPO_ROOT/tests/fixtures/metrics-synthetic.log" "$BRIDGE8/.metrics.log"
"$BRIDGE8/handlers/dashboard.sh" "/dashboard" >/dev/null 2>&1
DASH_OUT="$(recorded "$BRIDGE8")"
# No BOT_TOKEN in this offline bridge -> the IMAGE path's sendPhoto is a
# silent no-op (matches the rest of the repo's harmless-before-setup
# contract - see tg-send.sh); with no matplotlib in the default test
# interpreter it takes the TEXT path anyway, which the mock DOES record.
if [[ -n "$DASH_OUT" ]]; then
    ok "handlers/dashboard.sh sends a text dashboard when matplotlib is unavailable"
else
    # Only acceptable if matplotlib WAS available (image path -> no .env ->
    # silent no-op is then the correct, harmless-before-setup behavior).
    if python3 -c "import matplotlib" >/dev/null 2>&1; then
        ok "handlers/dashboard.sh took the image path (silent no-op with no BOT_TOKEN, as designed)"
    else
        fail "handlers/dashboard.sh sends a text dashboard when matplotlib is unavailable" "no output recorded"
    fi
fi
rm -rf "$BRIDGE8"

echo "== handlers/uptime.sh: proxy uptime from .metrics.log when no tg-poll.sh process is found =="
BRIDGE9="$(setup_temp_bridge)"
cp "$REPO_ROOT/tests/fixtures/metrics-synthetic.log" "$BRIDGE9/.metrics.log"
"$BRIDGE9/handlers/uptime.sh" "/uptime" >/dev/null 2>&1
UPTIME_OUT="$(recorded "$BRIDGE9")"
if [[ -n "$UPTIME_OUT" ]]; then
    ok "handlers/uptime.sh sends a reply (real process uptime or the honest proxy)"
else
    fail "handlers/uptime.sh sends a reply" "no output recorded"
fi
rm -rf "$BRIDGE9"

echo "== handlers/help.sh: lists commands live from relay.toml, never hardcoded/stale =="
BRIDGE10="$(setup_temp_bridge)"
cp "$REPO_ROOT/tests/fixtures/relay.toml" "$BRIDGE10/relay.toml"
"$BRIDGE10/handlers/help.sh" "/help" >/dev/null 2>&1
HELP_OUT="$(recorded "$BRIDGE10")"
if [[ "$HELP_OUT" == *"/status"* && "$HELP_OUT" == *"/example"* && "$HELP_OUT" == *"zero model tokens"* ]]; then
    ok "handlers/help.sh lists both forwarded (/status) and relay-handled (/example) commands"
else
    fail "handlers/help.sh lists both forwarded and relay-handled commands" "$HELP_OUT"
fi
rm -rf "$BRIDGE10"

# ============================================================================
echo "== lib/tts.sh + tg-send.sh: self-hosted TTS pipeline (offline, stubbed engines) =="
# NO network calls (this file's contract, see header) - a stub `curl` in a
# curated PATH records its args to a log file instead of hitting Telegram,
# and `piper`/`espeak-ng`/`ffmpeg` are stubbed the same way so the tests
# are deterministic regardless of what's actually installed on the host
# running them (real end-to-end verification is a separate, manual,
# clearly-marked live check - see the repo's TTS setup docs).

# A curated PATH containing only real, essential coreutils (found via the
# CURRENT PATH before we override it) plus whatever fake piper/espeak-ng/
# ffmpeg/curl a scenario adds - so "no engine installed" is genuinely true
# in the test even on a host that has real piper/espeak-ng/ffmpeg on its
# normal PATH.
tts_essential_path() {
    local dir="$1" tool real
    for tool in mktemp rm jq python3 dirname basename cat date head wc \
                grep sed cut tr mkdir env true false expr sleep; do
        real="$(command -v "$tool" 2>/dev/null || true)"
        [[ -n "$real" ]] && ln -sf "$real" "$dir/$tool" 2>/dev/null
    done
    printf '%s' "$dir"
}

# write_stub_curl <stub_dir> <log_file> - records every invocation's full
# argument list (one line per call) to <log_file>, always answers with a
# minimal Telegram-style success body, never touches the network.
write_stub_curl() {
    local dir="$1" log="$2"
    cat > "$dir/curl" <<STUB
#!/bin/bash
printf '%s\n' "\$*" >> "$log"
printf '{"ok":true,"result":{}}'
exit 0
STUB
    chmod +x "$dir/curl"
}

# write_stub_piper <stub_dir> - a fake `piper --model M --output_file F`
# that consumes stdin (the text) and writes non-empty fake WAV bytes to F.
write_stub_piper() {
    local dir="$1"
    cat > "$dir/piper" <<'STUB'
#!/bin/bash
OUT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output_file) OUT="$2"; shift 2 ;;
        *) shift ;;
    esac
done
cat >/dev/null
[[ -n "$OUT" ]] && printf 'RIFF_FAKE_WAV_DATA' > "$OUT"
exit 0
STUB
    chmod +x "$dir/piper"
}

# write_stub_espeak <stub_dir> - a fake `espeak-ng -w F "text"`.
write_stub_espeak() {
    local dir="$1"
    cat > "$dir/espeak-ng" <<'STUB'
#!/bin/bash
OUT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -w) OUT="$2"; shift 2 ;;
        *) shift ;;
    esac
done
[[ -n "$OUT" ]] && printf 'RIFF_FAKE_WAV_DATA' > "$OUT"
exit 0
STUB
    chmod +x "$dir/espeak-ng"
}

# write_stub_ffmpeg <stub_dir> - a fake `ffmpeg ... -i IN ... OUT` that
# "transcodes" (writes fake opus bytes to OUT) iff IN is non-empty.
write_stub_ffmpeg() {
    local dir="$1"
    cat > "$dir/ffmpeg" <<'STUB'
#!/bin/bash
IN="" OUT="" PREV=""
for a in "$@"; do
    [[ "$PREV" == "-i" ]] && IN="$a"
    PREV="$a"
done
OUT="${@: -1}"
if [[ -s "$IN" ]]; then
    printf 'OGG_FAKE_OPUS_DATA' > "$OUT"
    exit 0
fi
exit 1
STUB
    chmod +x "$dir/ffmpeg"
}

# setup_tts_bridge - like setup_temp_bridge, but symlinks the REAL
# tg-send.sh (not the recording mock) plus the real lib/ it needs, with a
# throwaway .env - so these tests exercise the actual TTS wiring, not a
# stand-in.
setup_tts_bridge() {
    local dir
    dir="$(mktemp -d)"
    mkdir -p "$dir/lib"
    ln -s "$REPO_ROOT/tg-send.sh" "$dir/tg-send.sh"
    ln -s "$REPO_ROOT/lib/relay-config.sh" "$dir/lib/relay-config.sh"
    ln -s "$REPO_ROOT/lib/relay-common.sh" "$dir/lib/relay-common.sh"
    ln -s "$REPO_ROOT/lib/tts.sh" "$dir/lib/tts.sh"
    ln -s "$REPO_ROOT/lib/toml_to_json.py" "$dir/lib/toml_to_json.py"
    cat > "$dir/.env" <<'ENV'
BOT_TOKEN=TEST_TOKEN_123
ALLOWED_USER_ID=999
ALLOWED_CHAT_ID=999
ENV
    chmod 600 "$dir/.env"
    printf '%s' "$dir"
}

# -- 1: mode=off (no relay.toml at all) -> byte-identical to pre-TTS
# -- behavior: sendMessage only, NEVER sendVoice/sendAudio, even with
# -- working engines on PATH.
TTS1="$(setup_tts_bridge)"
STUB1="$(mktemp -d)"; tts_essential_path "$STUB1" >/dev/null
LOG1="$(mktemp -u)"; write_stub_curl "$STUB1" "$LOG1"
write_stub_espeak "$STUB1"; write_stub_ffmpeg "$STUB1"
PATH="$STUB1" "$TTS1/tg-send.sh" "hello, no tts configured" >/dev/null 2>&1
if grep -q "sendMessage" "$LOG1" 2>/dev/null && ! grep -q "sendVoice\|sendAudio" "$LOG1" 2>/dev/null; then
    ok "tts: mode=off (no relay.toml) -> text only, no voice attempted, even with engines available"
else
    fail "tts: mode=off (no relay.toml) -> text only, no voice attempted" "$(cat "$LOG1" 2>/dev/null)"
fi
rm -rf "$TTS1" "$STUB1" "$LOG1"

# -- 2: mode="text+voice", espeak-ng+ffmpeg available -> BOTH sendMessage
# -- and sendVoice fire, voice as an .ogg file.
TTS2="$(setup_tts_bridge)"
cat > "$TTS2/relay.toml" <<'TOML'
[tts]
mode = "text+voice"
engine = "auto"
max_chars = 600
TOML
STUB2="$(mktemp -d)"; tts_essential_path "$STUB2" >/dev/null
LOG2="$(mktemp -u)"; write_stub_curl "$STUB2" "$LOG2"
write_stub_espeak "$STUB2"; write_stub_ffmpeg "$STUB2"
PATH="$STUB2" "$TTS2/tg-send.sh" "a short status update" >/dev/null 2>&1
if grep -q "sendMessage" "$LOG2" && grep -q "sendVoice" "$LOG2" && grep -q "voice=@.*\.ogg" "$LOG2"; then
    ok "tts: mode=text+voice -> sends text THEN a sendVoice with an .ogg file"
else
    fail "tts: mode=text+voice -> sends text then voice" "$(cat "$LOG2" 2>/dev/null)"
fi
rm -rf "$TTS2" "$STUB2" "$LOG2"

# -- 3: mode="voice-only", engine available -> ONLY sendVoice, no
# -- sendMessage at all.
TTS3="$(setup_tts_bridge)"
cat > "$TTS3/relay.toml" <<'TOML'
[tts]
mode = "voice-only"
engine = "auto"
max_chars = 600
TOML
STUB3="$(mktemp -d)"; tts_essential_path "$STUB3" >/dev/null
LOG3="$(mktemp -u)"; write_stub_curl "$STUB3" "$LOG3"
write_stub_espeak "$STUB3"; write_stub_ffmpeg "$STUB3"
PATH="$STUB3" "$TTS3/tg-send.sh" "a short status update" >/dev/null 2>&1
if grep -q "sendVoice" "$LOG3" && ! grep -q "sendMessage" "$LOG3"; then
    ok "tts: mode=voice-only + engine available -> voice only, text suppressed"
else
    fail "tts: mode=voice-only + engine available -> voice only" "$(cat "$LOG3" 2>/dev/null)"
fi
rm -rf "$TTS3" "$STUB3" "$LOG3"

# -- 4: mode="voice-only", NO engine at all -> falls back to text, never
# -- sends nothing; the skip is logged to .metrics.log (never-silent).
TTS4="$(setup_tts_bridge)"
cat > "$TTS4/relay.toml" <<'TOML'
[tts]
mode = "voice-only"
engine = "auto"
max_chars = 600
TOML
STUB4="$(mktemp -d)"; tts_essential_path "$STUB4" >/dev/null   # no piper/espeak-ng/ffmpeg
LOG4="$(mktemp -u)"; write_stub_curl "$STUB4" "$LOG4"
PATH="$STUB4" "$TTS4/tg-send.sh" "a short status update" >/dev/null 2>&1
if grep -q "sendMessage" "$LOG4" && ! grep -q "sendVoice\|sendAudio" "$LOG4"; then
    ok "tts: mode=voice-only + no engine -> falls back to text (never sends nothing)"
else
    fail "tts: mode=voice-only + no engine -> falls back to text" "$(cat "$LOG4" 2>/dev/null)"
fi
if grep -q "$(printf '\ttts\tskip\t')" "$TTS4/.metrics.log" 2>/dev/null; then
    ok "tts: no-engine skip is logged to .metrics.log (never-silent)"
else
    fail "tts: no-engine skip is logged to .metrics.log" "$(cat "$TTS4/.metrics.log" 2>/dev/null)"
fi
rm -rf "$TTS4" "$STUB4" "$LOG4"

# -- 5: max_chars guard - a message over the configured limit stays
# -- text-only even in text+voice mode.
TTS5="$(setup_tts_bridge)"
cat > "$TTS5/relay.toml" <<'TOML'
[tts]
mode = "text+voice"
engine = "auto"
max_chars = 10
TOML
STUB5="$(mktemp -d)"; tts_essential_path "$STUB5" >/dev/null
LOG5="$(mktemp -u)"; write_stub_curl "$STUB5" "$LOG5"
write_stub_espeak "$STUB5"; write_stub_ffmpeg "$STUB5"
PATH="$STUB5" "$TTS5/tg-send.sh" "this message is definitely longer than ten characters" >/dev/null 2>&1
if grep -q "sendMessage" "$LOG5" && ! grep -q "sendVoice\|sendAudio" "$LOG5"; then
    ok "tts: message over max_chars -> stays text-only (voice never attempted)"
else
    fail "tts: message over max_chars -> stays text-only" "$(cat "$LOG5" 2>/dev/null)"
fi
rm -rf "$TTS5" "$STUB5" "$LOG5"

# -- 6: ffmpeg absent -> the raw WAV is sent via sendAudio (still SOME
# -- voice), not sendVoice; never a hard failure.
TTS6="$(setup_tts_bridge)"
cat > "$TTS6/relay.toml" <<'TOML'
[tts]
mode = "text+voice"
engine = "auto"
max_chars = 600
TOML
STUB6="$(mktemp -d)"; tts_essential_path "$STUB6" >/dev/null
LOG6="$(mktemp -u)"; write_stub_curl "$STUB6" "$LOG6"
write_stub_espeak "$STUB6"   # no ffmpeg
PATH="$STUB6" "$TTS6/tg-send.sh" "a short status update" >/dev/null 2>&1
if grep -q "sendAudio" "$LOG6" && grep -q "audio=@.*\.wav" "$LOG6" && ! grep -q "sendVoice" "$LOG6"; then
    ok "tts: ffmpeg absent -> WAV sent via sendAudio fallback (still SOME voice)"
else
    fail "tts: ffmpeg absent -> WAV sent via sendAudio fallback" "$(cat "$LOG6" 2>/dev/null)"
fi
rm -rf "$TTS6" "$STUB6" "$LOG6"

# -- 7: pagination interaction - a multi-page message always skips TTS
# -- entirely, even in voice-only mode (falls back to the paginated text).
TTS7="$(setup_tts_bridge)"
cat > "$TTS7/relay.toml" <<'TOML'
[tts]
mode = "voice-only"
engine = "auto"
max_chars = 600
TOML
STUB7="$(mktemp -d)"; tts_essential_path "$STUB7" >/dev/null
LOG7="$(mktemp -u)"; write_stub_curl "$STUB7" "$LOG7"
write_stub_espeak "$STUB7"; write_stub_ffmpeg "$STUB7"
LONG_MSG="$(python3 -c "print('x' * 120)")"
PATH="$STUB7" TG_PAGE_SIZE=50 "$TTS7/tg-send.sh" "$LONG_MSG" >/dev/null 2>&1
SEND_COUNT="$(grep -c "sendMessage" "$LOG7" 2>/dev/null || echo 0)"
if [[ "$SEND_COUNT" -ge 2 ]] && ! grep -q "sendVoice\|sendAudio" "$LOG7"; then
    ok "tts: a paginated (multi-page) message always skips TTS - text-only"
else
    fail "tts: a paginated (multi-page) message always skips TTS" "pages=$SEND_COUNT log=$(cat "$LOG7" 2>/dev/null)"
fi
rm -rf "$TTS7" "$STUB7" "$LOG7"

echo "== lib/tts.sh: tts_select_engine() unit tests (engine preference/fallback) =="
# shellcheck disable=SC1091
source "$REPO_ROOT/lib/tts.sh"

STUB_ENGINE="$(mktemp -d)"; tts_essential_path "$STUB_ENGINE" >/dev/null
FAKE_MODEL="$(mktemp)"

RELAY_CONFIG_JSON='{"tts":{"engine":"auto"}}'
assert_empty "tts_select_engine: auto, neither piper nor espeak-ng installed -> nothing" \
    "$(PATH="$STUB_ENGINE" tts_select_engine)"

write_stub_espeak "$STUB_ENGINE"
RELAY_CONFIG_JSON='{"tts":{"engine":"auto"}}'
assert_eq "tts_select_engine: auto, only espeak-ng installed -> espeak" \
    "espeak" "$(PATH="$STUB_ENGINE" tts_select_engine)"

write_stub_piper "$STUB_ENGINE"
RELAY_CONFIG_JSON="{\"tts\":{\"engine\":\"auto\",\"voice_model\":\"$FAKE_MODEL\"}}"
assert_eq "tts_select_engine: auto, both installed + voice_model set -> prefers piper" \
    "piper" "$(PATH="$STUB_ENGINE" tts_select_engine)"

RELAY_CONFIG_JSON='{"tts":{"engine":"auto"}}'
assert_eq "tts_select_engine: auto, piper installed but NO voice_model configured -> falls back to espeak" \
    "espeak" "$(PATH="$STUB_ENGINE" tts_select_engine)"

RELAY_CONFIG_JSON='{"tts":{"engine":"piper"}}'
assert_empty "tts_select_engine: engine=piper explicit, no voice_model -> no silent fallback to espeak" \
    "$(PATH="$STUB_ENGINE" tts_select_engine)"

RELAY_CONFIG_JSON='{"tts":{"engine":"espeak"}}'
assert_eq "tts_select_engine: engine=espeak explicit -> espeak regardless of piper's availability" \
    "espeak" "$(PATH="$STUB_ENGINE" tts_select_engine)"

RELAY_CONFIG_JSON="{}"
rm -rf "$STUB_ENGINE" "$FAKE_MODEL"

# ============================================================================
echo "== lib/metrics_agg.py + lib/dashboard_render.py: Python unit tests (aggregation + image/fallback) =="
if command -v python3 >/dev/null 2>&1; then
    PY_OUT="$(python3 "$REPO_ROOT/tests/test_metrics_agg.py" 2>&1)"
    PY_RC=$?
    if [[ $PY_RC -eq 0 ]]; then
        ok "python3 tests/test_metrics_agg.py"
    else
        fail "python3 tests/test_metrics_agg.py" "$PY_OUT"
    fi
else
    printf 'SKIP  python3 not installed - skipping aggregation unit tests (never-silent: this line IS the record)\n'
fi

# ============================================================================
echo
echo "============================================================"
printf 'Total: %d   Pass: %d   Fail: %d\n' "$((PASS + FAIL))" "$PASS" "$FAIL"
if (( FAIL > 0 )); then
    printf 'FAILED: %s\n' "${FAILED_NAMES[*]}"
    exit 1
fi
exit 0

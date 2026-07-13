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
    ln -s "$REPO_ROOT/lib/usage_ingest.py" "$dir/lib/usage_ingest.py"
    ln -s "$REPO_ROOT/lib/format.sh" "$dir/lib/format.sh"
    ln -s "$REPO_ROOT/lib/code_highlight.sh" "$dir/lib/code_highlight.sh"
    ln -s "$REPO_ROOT/lib/code_highlight.py" "$dir/lib/code_highlight.py"
    ln -s "$REPO_ROOT/handlers/example-echo.sh" "$dir/handlers/example-echo.sh"
    ln -s "$REPO_ROOT/handlers/dashboard.sh" "$dir/handlers/dashboard.sh"
    ln -s "$REPO_ROOT/handlers/stats.sh" "$dir/handlers/stats.sh"
    ln -s "$REPO_ROOT/handlers/uptime.sh" "$dir/handlers/uptime.sh"
    ln -s "$REPO_ROOT/handlers/help.sh" "$dir/handlers/help.sh"
    ln -s "$REPO_ROOT/handlers/usage.sh" "$dir/handlers/usage.sh"

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
         lib/relay-common.sh lib/relay-config.sh lib/claude-code-events.sh lib/tts.sh lib/format.sh lib/code_highlight.sh \
         handlers/dashboard.sh handlers/stats.sh handlers/uptime.sh handlers/help.sh handlers/usage.sh; do
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
             lib/relay-common.sh lib/relay-config.sh lib/claude-code-events.sh lib/tts.sh lib/format.sh lib/code_highlight.sh \
             handlers/dashboard.sh handlers/stats.sh handlers/uptime.sh handlers/help.sh handlers/usage.sh; do
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
echo "== handlers/usage.sh: DEFAULT OFF (opt-in) - no relay.toml at all -> disabled reply, never a crash =="
BRIDGE11="$(setup_temp_bridge)"
"$BRIDGE11/handlers/usage.sh" "/usage" >/dev/null 2>&1
USAGE_OFF_OUT="$(recorded "$BRIDGE11")"
if [[ "$USAGE_OFF_OUT" == *"disabled"* ]]; then
    ok "handlers/usage.sh with no relay.toml replies that usage tracking is disabled (never silent, never enabled by accident)"
else
    fail "handlers/usage.sh with no relay.toml replies that usage tracking is disabled" "$USAGE_OFF_OUT"
fi
rm -rf "$BRIDGE11"

echo "== handlers/usage.sh: [usage].enabled = true against the SYNTHETIC fixture tree =="
BRIDGE12="$(setup_temp_bridge)"
cat > "$BRIDGE12/relay.toml" <<TOML
[usage]
enabled = true
source = "claude-code"
projects_dir = "$REPO_ROOT/tests/fixtures/usage-synthetic"
window = "all"
TOML
"$BRIDGE12/handlers/usage.sh" "/usage" >/dev/null 2>&1
USAGE_ON_OUT="$(recorded "$BRIDGE12")"
# No BOT_TOKEN in this offline bridge -> the IMAGE path's sendPhoto is a
# silent no-op (same harmless-before-setup contract as handlers/dashboard.sh);
# with no matplotlib in the interpreter it takes the TEXT path, which the
# mock DOES record.
if [[ -n "$USAGE_ON_OUT" ]]; then
    ok "handlers/usage.sh enabled sends a real token-usage reply (text fallback) over the synthetic fixture tree"
else
    if python3 -c "import matplotlib" >/dev/null 2>&1; then
        ok "handlers/usage.sh enabled took the image path (silent no-op with no BOT_TOKEN, as designed)"
    else
        fail "handlers/usage.sh enabled sends a real token-usage reply" "no output recorded"
    fi
fi
# Never leaves an un-gitignored artifact behind - the cache dir must exist
# under .usage/ (matches .gitignore's "Token-usage cache/data" block).
if [[ -f "$BRIDGE12/.usage/usage-summary.json" ]]; then
    ok "handlers/usage.sh writes its cache under .usage/ (the gitignored path)"
else
    fail "handlers/usage.sh writes its cache under .usage/" "no cache file found"
fi
rm -rf "$BRIDGE12"

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

# write_stub_piper_logged <stub_dir> <log_file> - like write_stub_piper, but
# also records its own full argument list (one line per call) to <log_file>
# - used to assert which flags tts_synthesize actually passes piper (e.g.
# --length-scale).
write_stub_piper_logged() {
    local dir="$1" log="$2"
    cat > "$dir/piper" <<STUB
#!/bin/bash
printf '%s\n' "\$*" >> "$log"
OUT=""
while [[ \$# -gt 0 ]]; do
    case "\$1" in
        --output_file) OUT="\$2"; shift 2 ;;
        *) shift ;;
    esac
done
cat >/dev/null
[[ -n "\$OUT" ]] && printf 'RIFF_FAKE_WAV_DATA' > "\$OUT"
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
# stand-in. Also symlinks the real lib/format.sh (the structured-format
# layer runs on the SAME real tg-send.sh path TTS does) so these tests
# exercise the real format+TTS interaction, not the "lib missing ->
# passthrough shim" fallback.
setup_tts_bridge() {
    local dir
    dir="$(mktemp -d)"
    mkdir -p "$dir/lib"
    ln -s "$REPO_ROOT/tg-send.sh" "$dir/tg-send.sh"
    ln -s "$REPO_ROOT/lib/relay-config.sh" "$dir/lib/relay-config.sh"
    ln -s "$REPO_ROOT/lib/relay-common.sh" "$dir/lib/relay-common.sh"
    ln -s "$REPO_ROOT/lib/tts.sh" "$dir/lib/tts.sh"
    ln -s "$REPO_ROOT/lib/format.sh" "$dir/lib/format.sh"
    ln -s "$REPO_ROOT/lib/code_highlight.sh" "$dir/lib/code_highlight.sh"
    ln -s "$REPO_ROOT/lib/code_highlight.py" "$dir/lib/code_highlight.py"
    ln -s "$REPO_ROOT/lib/toml_to_json.py" "$dir/lib/toml_to_json.py"
    cat > "$dir/.env" <<'ENV'
BOT_TOKEN=TEST_TOKEN_123
ALLOWED_USER_ID=999
ALLOWED_CHAT_ID=999
ENV
    chmod 600 "$dir/.env"
    printf '%s' "$dir"
}

# setup_format_bridge - like setup_tts_bridge, but WITHOUT lib/tts.sh (TTS
# stays the "lib missing" no-voice shim - irrelevant to these tests) and
# with a stub `curl` pre-installed on PATH (see write_stub_curl above) so
# every test below can inspect exactly what tg-send.sh's real HTML-format
# (and code-image) pipeline POSTs, with no network call. Includes the real
# lib/code_highlight.sh + lib/code_highlight.py (not the passthrough shim) so
# these tests exercise the actual code-image wiring too.
setup_format_bridge() {
    local dir
    dir="$(mktemp -d)"
    mkdir -p "$dir/lib"
    ln -s "$REPO_ROOT/tg-send.sh" "$dir/tg-send.sh"
    ln -s "$REPO_ROOT/lib/relay-config.sh" "$dir/lib/relay-config.sh"
    ln -s "$REPO_ROOT/lib/relay-common.sh" "$dir/lib/relay-common.sh"
    ln -s "$REPO_ROOT/lib/format.sh" "$dir/lib/format.sh"
    ln -s "$REPO_ROOT/lib/code_highlight.sh" "$dir/lib/code_highlight.sh"
    ln -s "$REPO_ROOT/lib/code_highlight.py" "$dir/lib/code_highlight.py"
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

echo "== lib/tts.sh: _tts_pitch_filter() unit tests (optional depth knob) =="

RELAY_CONFIG_JSON='{"tts":{}}'
assert_empty "pitch filter: unset -> no filter (today's byte-identical behavior)" \
    "$(_tts_pitch_filter 22050)"

RELAY_CONFIG_JSON='{"tts":{"pitch":"0"}}'
assert_empty "pitch filter: pitch=0 -> no filter" \
    "$(_tts_pitch_filter 22050)"

RELAY_CONFIG_JSON='{"tts":{"pitch":"not-a-number"}}'
assert_empty "pitch filter: non-numeric pitch -> no filter (never a bad ffmpeg arg)" \
    "$(_tts_pitch_filter 22050)"

RELAY_CONFIG_JSON='{"tts":{"pitch":"-1.5"}}'
assert_eq "pitch filter: negative semitones -> asetrate/aresample/atempo, duration-preserving" \
    "asetrate=22050*0.917004,aresample=22050,atempo=1/0.917004" \
    "$(_tts_pitch_filter 22050)"

RELAY_CONFIG_JSON='{"tts":{"pitch":"-1.5"}}'
assert_empty "pitch filter: no/invalid sample rate -> no filter (skip-graceful, never a malformed -af)" \
    "$(_tts_pitch_filter '')"

RELAY_CONFIG_JSON="{}"

echo "== lib/tts.sh: tts_synthesize() piper --length-scale cadence knob =="

STUB_LS="$(mktemp -d)"; tts_essential_path "$STUB_LS" >/dev/null
LOG_LS="$(mktemp -u)"
write_stub_piper_logged "$STUB_LS" "$LOG_LS"
FAKE_MODEL_LS="$(mktemp)"
OUT_WAV_LS="$(mktemp -u)"

RELAY_CONFIG_JSON="{\"tts\":{\"voice_model\":\"$FAKE_MODEL_LS\"}}"
PATH="$STUB_LS" tts_synthesize piper "cadence test" "$OUT_WAV_LS" >/dev/null 2>&1
if ! grep -q -- "--length-scale" "$LOG_LS"; then
    ok "tts_synthesize: length_scale unset -> no --length-scale flag (unchanged default)"
else
    fail "tts_synthesize: length_scale unset -> no --length-scale flag" "$(cat "$LOG_LS")"
fi
rm -f "$LOG_LS" "$OUT_WAV_LS"

RELAY_CONFIG_JSON="{\"tts\":{\"voice_model\":\"$FAKE_MODEL_LS\",\"length_scale\":\"0.9\"}}"
PATH="$STUB_LS" tts_synthesize piper "cadence test" "$OUT_WAV_LS" >/dev/null 2>&1
if grep -q -- "--length-scale 0.9" "$LOG_LS"; then
    ok "tts_synthesize: length_scale=0.9 -> passed through to piper"
else
    fail "tts_synthesize: length_scale=0.9 -> passed through to piper" "$(cat "$LOG_LS" 2>/dev/null)"
fi
rm -f "$LOG_LS" "$OUT_WAV_LS"

RELAY_CONFIG_JSON="{\"tts\":{\"voice_model\":\"$FAKE_MODEL_LS\",\"length_scale\":\"not-a-number\"}}"
PATH="$STUB_LS" tts_synthesize piper "cadence test" "$OUT_WAV_LS" >/dev/null 2>&1
if ! grep -q -- "--length-scale" "$LOG_LS"; then
    ok "tts_synthesize: non-numeric length_scale -> flag omitted (never a bad piper arg)"
else
    fail "tts_synthesize: non-numeric length_scale -> flag omitted" "$(cat "$LOG_LS" 2>/dev/null)"
fi

RELAY_CONFIG_JSON="{}"
rm -rf "$STUB_LS" "$FAKE_MODEL_LS"
rm -f "$LOG_LS" "$OUT_WAV_LS"

# ============================================================================
echo "== lib/format.sh: structured-formatting layer (unit tests, sourced directly) =="
# shellcheck disable=SC1091
source "$REPO_ROOT/lib/relay-config.sh"
# shellcheck disable=SC1091
source "$REPO_ROOT/lib/relay-common.sh"
# shellcheck disable=SC1091
source "$REPO_ROOT/lib/format.sh"
RELAY_CONFIG_JSON="{}"
FMT_HEADERS=true FMT_CODE_SPANS=true FMT_BLOCKQUOTES=true FMT_SOFT_WRAP=true

# -- escaping ----------------------------------------------------------------
assert_eq "escape: < > & all escaped, in order (no double-escape)" \
    "a &lt; b &amp; c &gt; d" \
    "$(_fmt_escape_html 'a < b & c > d')"

assert_eq "escape: an existing literal <code> tag in the SOURCE text is neutralized (not left live)" \
    "&lt;code&gt;danger&lt;/code&gt;" \
    "$(_fmt_escape_html '<code>danger</code>')"

RELAY_CONFIG_JSON="{}"
format_message 'ok <b>not actually bold</b> & <script>bad</script>'
assert_eq "format_message: literal HTML-looking source text is escaped, never live markup" \
    "ok &lt;b&gt;not actually bold&lt;/b&gt; &amp; &lt;script&gt;bad&lt;/script&gt;" \
    "$FMT_TEXT"
assert_eq "format_message: parse_mode is HTML by default" "HTML" "$FMT_PARSE_MODE"

# -- soft-wrap -----------------------------------------------------------------
SHORT_LINE="short line, well under fifty chars"
assert_eq "wrap: a line already <= wrap_width is untouched (single line, no split)" \
    "$SHORT_LINE" \
    "$(_fmt_wrap_line "$SHORT_LINE" 50)"

LONG_LINE="this is a much longer line of plain prose that will need to be wrapped at word boundaries to fit a phone screen nicely"
WRAPPED="$(_fmt_wrap_line "$LONG_LINE" 50)"
WRAP_LINE_COUNT=$(printf '%s\n' "$WRAPPED" | wc -l)
WRAP_MAX_LEN=$(printf '%s\n' "$WRAPPED" | awk '{ print length }' | sort -rn | head -1)
if (( WRAP_LINE_COUNT > 1 )) && (( WRAP_MAX_LEN <= 50 )) \
    && [[ "$(printf '%s' "$WRAPPED" | tr '\n' ' ')" == *"$LONG_LINE"* || "$(printf '%s' "$WRAPPED" | tr -d '\n')" == "$(printf '%s' "$LONG_LINE" | tr -d ' ')" ]]; then
    ok "wrap: a long prose line splits into multiple lines, each <= wrap_width, no word lost/altered"
else
    fail "wrap: long line splits at word boundaries within width" "lines=$WRAP_LINE_COUNT maxlen=$WRAP_MAX_LEN out=[$WRAPPED]"
fi

URL_LINE="see https://example.com/a/very/long/path/that/would/never/fit/in/fifty/characters/at/all for details"
URL_WRAPPED="$(_fmt_wrap_line "$URL_LINE" 50)"
if [[ "$URL_WRAPPED" == *"https://example.com/a/very/long/path/that/would/never/fit/in/fifty/characters/at/all"* ]]; then
    ok "wrap: a URL is never broken mid-URL, even though it exceeds wrap_width alone"
else
    fail "wrap: URL kept whole" "$URL_WRAPPED"
fi

CODE_SPAN_LINE='run the `some very long command with lots of embedded spaces inside` now please'
CODE_WRAPPED="$(_fmt_wrap_line "$CODE_SPAN_LINE" 50)"
if [[ "$CODE_WRAPPED" == *'`some very long command with lots of embedded spaces inside`'* ]]; then
    ok "wrap: an inline \`code span\` (even with embedded spaces) is never broken mid-span"
else
    fail "wrap: code span kept whole" "$CODE_WRAPPED"
fi

# -- header / blockquote / emphasis rendering --------------------------------
RELAY_CONFIG_JSON="{}"
format_message "## Explicit Header
prose line"
assert_eq "header: explicit '## ' prefix -> bolded, prefix stripped" \
    "<b>Explicit Header</b>
prose line" \
    "$FMT_TEXT"

format_message "✅ BUILD FINISHED"
assert_eq "header: leading-emoji ALL-CAPS short line -> bolded" \
    "<b>✅ BUILD FINISHED</b>" \
    "$FMT_TEXT"

format_message "🚀 Deploy Started"
assert_eq "header: leading-emoji Title-Case short line -> bolded" \
    "<b>🚀 Deploy Started</b>" \
    "$FMT_TEXT"

format_message "✅ code-reviewer finished — Found 2 issues, both low severity"
if [[ "$FMT_TEXT" != *"<b>"* ]]; then
    ok "header: a leading-emoji SENTENCE (lowercase, not all-caps/title-case) is NOT bolded"
else
    fail "header: ordinary sentence must not be treated as a header" "$FMT_TEXT"
fi

format_message "> a quoted note
> spanning two lines"
assert_eq "blockquote: consecutive '> ' lines group into ONE <blockquote>" \
    "<blockquote>a quoted note
spanning two lines</blockquote>" \
    "$FMT_TEXT"

LONG_QUOTE_SRC="> line one
> line two
> line three
> line four"
format_message "$LONG_QUOTE_SRC"
if [[ "$FMT_TEXT" == "<blockquote expandable>"* ]]; then
    ok "blockquote: a >3-line quote becomes an EXPANDABLE blockquote"
else
    fail "blockquote: long quote should be expandable" "$FMT_TEXT"
fi

format_message "this is *emphasis* and this is _also italic_"
assert_eq "emphasis: both *word* and _word_ render as <i>...</i>" \
    "this is <i>emphasis</i> and this is <i>also italic</i>" \
    "$FMT_TEXT"

format_message "the identifier my_var_name must stay literal"
if [[ "$FMT_TEXT" == *"my_var_name"* && "$FMT_TEXT" != *"<i>"* ]]; then
    ok "emphasis: a snake_case identifier (unpaired/word-internal underscores) is never mistaken for italic"
else
    fail "emphasis: snake_case must not be treated as emphasis" "$FMT_TEXT"
fi

# -- parse_mode="none" / enabled=false passthrough ---------------------------
RELAY_CONFIG_JSON='{"format":{"parse_mode":"none"}}'
RAW='## not a header <literally> & such'
format_message "$RAW"
assert_eq "parse_mode=none: text passes through completely UNCHANGED" "$RAW" "$FMT_TEXT"
assert_eq "parse_mode=none: FMT_PARSE_MODE is empty (no parse_mode param sent)" "" "$FMT_PARSE_MODE"

RELAY_CONFIG_JSON='{"format":{"enabled":false}}'
format_message "$RAW"
assert_eq "enabled=false: text passes through completely UNCHANGED" "$RAW" "$FMT_TEXT"
assert_eq "enabled=false: FMT_PARSE_MODE is empty" "" "$FMT_PARSE_MODE"
RELAY_CONFIG_JSON="{}"

# -- fenced code blocks: verbatim, language classes, mycelium tags ----------
# NOTE: "verbatim" means never reflowed/wrapped/marked-up - it does NOT
# exempt code content from the same three-character HTML escape every
# other code path uses (< > &), since parse_mode=HTML would otherwise
# mis-render (or Telegram would reject) a literal `<`/`>`/`&` in the
# source. These expected strings run the same RUST_LONG_LINE/MYC_LINE
# through _fmt_escape_html so the test can't silently drift from that
# contract.
RUST_LONG_LINE="fn this_is_a_very_long_rust_line_that_would_definitely_exceed_the_fifty_char_wrap_width(x: i32) -> i32 { x }"
format_message "before

\`\`\`rust
${RUST_LONG_LINE}
\`\`\`

after"
assert_eq "fenced rust block -> language-rust code box, content byte-for-byte VERBATIM (never wrapped, even far past wrap_width) + still HTML-escaped" \
    "before

<pre><code class=\"language-rust\">$(_fmt_escape_html "$RUST_LONG_LINE")</code></pre>

after" \
    "$FMT_TEXT"

MYC_LINE1="nodule example"
MYC_LINE2="  fn f(x) -> x"
format_message "\`\`\`myc
${MYC_LINE1}
${MYC_LINE2}
\`\`\`"
assert_eq "fenced \`\`\`myc block -> language-rust by DEFAULT (Telegram's client highlighter doesn't know 'mycelium' yet; rust is the closest-family alias - see [code_highlight].myc_inline_lang), escaped verbatim" \
    "<pre><code class=\"language-rust\">$(_fmt_escape_html "$MYC_LINE1")
$(_fmt_escape_html "$MYC_LINE2")</code></pre>" \
    "$FMT_TEXT"

format_message "\`\`\`mycelium
nodule example2
\`\`\`"
assert_eq "fenced \`\`\`mycelium block also aliases to language-rust by default" \
    '<pre><code class="language-rust">nodule example2</code></pre>' \
    "$FMT_TEXT"

RELAY_CONFIG_JSON='{"code_highlight":{"myc_inline_lang":"mycelium"}}'
format_message "\`\`\`myc
nodule example3
\`\`\`"
assert_eq "myc_inline_lang=\"mycelium\" opts back into the literal, unaliased language-mycelium tag" \
    '<pre><code class="language-mycelium">nodule example3</code></pre>' \
    "$FMT_TEXT"
RELAY_CONFIG_JSON="{}"

RELAY_CONFIG_JSON='{"code_highlight":{"myc_inline_lang":""}}'
format_message "\`\`\`myc
nodule example4
\`\`\`"
assert_eq "myc_inline_lang=\"\" (explicitly empty) is indistinguishable from unset to cfg_get's own [[ -n ]] check (a pre-existing, repo-wide limitation - see lib/relay-config.sh) - falls through to the key's own default, language-rust, same as leaving it unset" \
    '<pre><code class="language-rust">nodule example4</code></pre>' \
    "$FMT_TEXT"
RELAY_CONFIG_JSON="{}"

RELAY_CONFIG_JSON='{"code_highlight":{"myc_inline_lang":"python"}}'
format_message "\`\`\`myc
nodule example5
\`\`\`"
assert_eq "myc_inline_lang can alias to any ALLOWLISTED language, not just rust/mycelium" \
    '<pre><code class="language-python">nodule example5</code></pre>' \
    "$FMT_TEXT"
RELAY_CONFIG_JSON="{}"

# -- Fail-closed regression guard: myc_inline_lang crosses a trust
# -- boundary (relay.toml is local config today, but this must not rely
# -- on that) straight into `class="language-%s"`. An unrecognized/
# -- malformed value must NOT be passed through verbatim - it must fall
# -- back to the safe default already established ("mycelium" itself),
# -- validated against the same _fmt_known_lang allowlist real fence
# -- tags go through.
RELAY_CONFIG_JSON='{"code_highlight":{"myc_inline_lang":"totally-not-a-real-lang<script>"}}'
format_message "\`\`\`myc
nodule example6
\`\`\`"
assert_eq "myc_inline_lang: an unrecognized/adversarial config value fails CLOSED (falls back to language-mycelium, never passed through unchecked)" \
    '<pre><code class="language-mycelium">nodule example6</code></pre>' \
    "$FMT_TEXT"
RELAY_CONFIG_JSON="{}"

format_message "\`\`\`totallymadeupxyz
some content
\`\`\`"
assert_eq "fenced block with an UNRECOGNIZED language tag -> still boxed, but plain <pre> (no class)" \
    "<pre>some content</pre>" \
    "$FMT_TEXT"

MIXED_LONG="this paragraph of prose is definitely long enough that it must be wrapped across more than one line on a phone-width screen"
MIXED_CODE_LINE="fn keep_this_line_totally_unwrapped_even_though_it_is_long(x) -> x"
format_message "## Findings

${MIXED_LONG}

\`\`\`myc
${MIXED_CODE_LINE}
\`\`\`"
MIXED_LINES=$(printf '%s\n' "$FMT_TEXT" | wc -l)
if [[ "$FMT_TEXT" == *"<b>Findings</b>"* ]] \
    && [[ "$FMT_TEXT" == *"<pre><code class=\"language-rust\">$(_fmt_escape_html "$MIXED_CODE_LINE")</code></pre>"* ]] \
    && (( MIXED_LINES > 5 )); then
    ok "mixed message: prose is soft-wrapped AND the fenced code block is preserved verbatim, in one message"
else
    fail "mixed message: prose wrap + verbatim code block" "$FMT_TEXT"
fi
_fmt_html_balanced "$FMT_TEXT" && ok "mixed message: final HTML is tag-balanced" || fail "mixed message: HTML balance check" "$FMT_TEXT"

# -- unclosed fence at EOF: never a stray/empty <pre></pre> box (#12 review) -
# -- the opening marker + any collected body falls back to literal text.
format_message "before

\`\`\`
after this is unclosed forever"
assert_eq "an unclosed fence with NO body at all -> literal text, never an empty <pre></pre> box" \
    "before

\`\`\`
after this is unclosed forever" \
    "$FMT_TEXT"

format_message "\`\`\`python
def f():
    pass"
assert_eq "an unclosed fence WITH a body -> the opening marker + body as literal escaped text, not a code box" \
    "\`\`\`python
def f():
    pass" \
    "$FMT_TEXT"

# -- never-silent (G2): force _fmt_html_balanced to fail and confirm the
# -- escaped-plain-text fallback actually fires (previously reasoning-
# -- verified but untested - #12 review LOW).
_FMT_HTML_BALANCED_ORIG="$(declare -f _fmt_html_balanced)"
_fmt_html_balanced() { return 1; }
BAL_FAIL_SRC="## Header
prose with <em>literal-looking</em> markup"
format_message "$BAL_FAIL_SRC"
if [[ "$FMT_TEXT" == "$(_fmt_escape_html "$BAL_FAIL_SRC")" && "$FMT_PARSE_MODE" == "HTML" ]]; then
    ok "never-silent: a forced HTML-balance failure falls back to escaped plain text (parse_mode stays HTML)"
else
    fail "never-silent: forced balance-check failure fallback" "FMT_TEXT=$FMT_TEXT FMT_PARSE_MODE=$FMT_PARSE_MODE"
fi
eval "$_FMT_HTML_BALANCED_ORIG"

RELAY_CONFIG_JSON="{}"

# -- end-to-end via the real tg-send.sh pipeline (stubbed curl, no network) --
echo "== lib/format.sh + tg-send.sh: end-to-end (real pipeline, stubbed curl) =="
STUB_FMT="$(mktemp -d)"; tts_essential_path "$STUB_FMT" >/dev/null
LOG_FMT="$(mktemp -u)"; write_stub_curl "$STUB_FMT" "$LOG_FMT"
FMT1="$(setup_format_bridge)"
PATH="$STUB_FMT" "$FMT1/tg-send.sh" "## Header
some prose"
if grep -q 'text=<b>Header</b>' "$LOG_FMT" && grep -q 'parse_mode=HTML' "$LOG_FMT"; then
    ok "e2e: default (no relay.toml) -> formatting ON, HTML parse_mode sent"
else
    fail "e2e: default formatting ON" "$(cat "$LOG_FMT" 2>/dev/null)"
fi
rm -rf "$FMT1" "$STUB_FMT" "$LOG_FMT"

STUB_FMT2="$(mktemp -d)"; tts_essential_path "$STUB_FMT2" >/dev/null
LOG_FMT2="$(mktemp -u)"; write_stub_curl "$STUB_FMT2" "$LOG_FMT2"
FMT2="$(setup_format_bridge)"
cat > "$FMT2/relay.toml" <<'TOML'
[format]
parse_mode = "none"
TOML
PATH="$STUB_FMT2" "$FMT2/tg-send.sh" "## Header
some prose"
if grep -q 'text=## Header' "$LOG_FMT2" && ! grep -q 'parse_mode=' "$LOG_FMT2"; then
    ok "e2e: parse_mode=none -> plain text sent, byte-identical to pre-format behavior, no parse_mode param"
else
    fail "e2e: parse_mode=none passthrough" "$(cat "$LOG_FMT2" 2>/dev/null)"
fi
rm -rf "$FMT2" "$STUB_FMT2" "$LOG_FMT2"

# A multi-page send: the "[k/n]" header stays intact and is bolded when
# formatting is active.
STUB_FMT3="$(mktemp -d)"; tts_essential_path "$STUB_FMT3" >/dev/null
LOG_FMT3="$(mktemp -u)"; write_stub_curl "$STUB_FMT3" "$LOG_FMT3"
FMT3="$(setup_format_bridge)"
PAGINATED_MSG="$(python3 -c "print('word ' * 60)")"
PATH="$STUB_FMT3" TG_PAGE_SIZE=100 "$FMT3/tg-send.sh" "$PAGINATED_MSG" >/dev/null 2>&1
if grep -q 'text=<b>\[1/' "$LOG_FMT3"; then
    ok "e2e: multi-page send bolds the [k/n] pagination header"
else
    fail "e2e: [k/n] header bolded" "$(cat "$LOG_FMT3" 2>/dev/null)"
fi
rm -rf "$FMT3" "$STUB_FMT3" "$LOG_FMT3"

# Never-silent: a Telegram-side HTML rejection retries ONCE as plain text,
# and the fallback is logged (never a dropped message, never a hung retry
# loop).
STUB_FMT4="$(mktemp -d)"; tts_essential_path "$STUB_FMT4" >/dev/null
LOG_FMT4="$(mktemp -u)"
cat > "$STUB_FMT4/curl" <<STUB
#!/bin/bash
if [[ "\$*" == *"parse_mode=HTML"* ]]; then
    printf '{"ok":false,"description":"Bad Request: can'"'"'t parse entities"}'
else
    printf '{"ok":true,"result":{}}' >> "$LOG_FMT4"
fi
exit 0
STUB
chmod +x "$STUB_FMT4/curl"
FMT4="$(setup_format_bridge)"
PATH="$STUB_FMT4" "$FMT4/tg-send.sh" "## Header
prose"
if [[ -s "$LOG_FMT4" ]] && grep -q "format	send_fallback" "$FMT4/.metrics.log" 2>/dev/null; then
    ok "e2e: a Telegram HTML-parse rejection retries ONCE as plain text (never dropped) and logs the fallback"
else
    fail "e2e: HTML-rejection retry-as-plain-text" "log=$(cat "$LOG_FMT4" 2>/dev/null) metrics=$(cat "$FMT4/.metrics.log" 2>/dev/null)"
fi
rm -rf "$FMT4" "$STUB_FMT4" "$LOG_FMT4"

# ============================================================================
echo "== lib/code_highlight.sh + tg-send.sh: host-highlighted code-doc e2e (real pipeline, stubbed curl) =="

# -- default (no relay.toml): mode="inline-only" -> the v0.3.0 inline box
# -- still sends (myc/mycelium ALIASED to language-rust by default - see
# -- lib/format.sh), and NO sendDocument at all - this file stays a no-op.
STUB_IMG1="$(mktemp -d)"; tts_essential_path "$STUB_IMG1" >/dev/null
LOG_IMG1="$(mktemp -u)"; write_stub_curl "$STUB_IMG1" "$LOG_IMG1"
IMG1="$(setup_format_bridge)"
PATH="$STUB_IMG1" "$IMG1/tg-send.sh" "## Findings

\`\`\`myc
nodule example
fn f(x) -> x
\`\`\`

done"
if grep -q 'sendMessage' "$LOG_IMG1" && grep -q 'language-rust' "$LOG_IMG1"; then
    ok "code-highlight e2e: default mode=inline-only sends the v0.3.0 inline box (myc aliased to language-rust)"
else
    fail "code-highlight e2e: default inline box" "$(cat "$LOG_IMG1" 2>/dev/null)"
fi
if grep -q 'sendDocument' "$LOG_IMG1"; then
    fail "code-highlight e2e: default mode=inline-only must NOT send a document" "$(cat "$LOG_IMG1" 2>/dev/null)"
else
    ok "code-highlight e2e: default mode=inline-only sends no sendDocument"
fi
rm -rf "$IMG1" "$STUB_IMG1" "$LOG_IMG1"

# -- mode="html-doc", pygments present -> the v0.3.0 inline box STILL sends
# -- (unchanged, unaffected) AND a highlighted HTML document follows via
# -- sendDocument, paired with a <pre> caption.
STUB_IMG2="$(mktemp -d)"; tts_essential_path "$STUB_IMG2" >/dev/null
LOG_IMG2="$(mktemp -u)"; write_stub_curl "$STUB_IMG2" "$LOG_IMG2"
IMG2="$(setup_format_bridge)"
cat > "$IMG2/relay.toml" <<'TOML'
[code_highlight]
mode = "html-doc"
TOML
PATH="$STUB_IMG2" "$IMG2/tg-send.sh" '```myc
nodule example
fn f(x) -> x
```'
if command -v python3 >/dev/null 2>&1 && python3 -c 'import pygments, pygments.formatters' >/dev/null 2>&1; then
    if grep -q 'sendMessage' "$LOG_IMG2" && grep 'sendMessage' "$LOG_IMG2" | grep -q 'language-rust'; then
        ok "code-highlight e2e: mode=html-doc still sends the (unchanged) v0.3.0 inline box"
    else
        fail "code-highlight e2e: mode=html-doc inline box unaffected" "$(cat "$LOG_IMG2" 2>/dev/null)"
    fi
    if grep -q 'sendDocument' "$LOG_IMG2" && grep 'sendDocument' "$LOG_IMG2" | grep -q 'document=@.*\.html;filename=snippet\.myc\.html'; then
        ok "code-highlight e2e: mode=html-doc ALSO sends a snippet.myc.html document via sendDocument"
    else
        fail "code-highlight e2e: mode=html-doc sendDocument" "$(cat "$LOG_IMG2" 2>/dev/null)"
    fi
    if grep 'sendDocument' "$LOG_IMG2" | grep -q 'caption=.*language-rust'; then
        ok "code-highlight e2e: default keep_text=caption pairs a copyable <pre> caption with the document"
    else
        fail "code-highlight e2e: document caption" "$(cat "$LOG_IMG2" 2>/dev/null)"
    fi
    # -- Regression guard for the curl `-F "caption=<...>" ` exit-26
    # -- message-drop bug: the caption ALWAYS starts with the literal `<`
    # -- (it's _fmt_render_code_block's `<pre>...` HTML), and classic
    # -- `curl -F name=value` treats a value starting with `<`/`@` as
    # -- "read this from a local file" and aborts before any network call.
    # -- write_stub_curl above can't reproduce that (it isn't real curl -
    # -- it just logs argv and always answers ok:true), so this asserts
    # -- directly on the CONSTRUCTED argv: the caption field must be sent
    # -- via `--form-string` (curl's documented literal-text mechanism),
    # -- never a bare `-F caption=...`.
    if grep 'sendDocument' "$LOG_IMG2" | grep -q -- '--form-string caption='; then
        ok "code-highlight e2e: sendDocument caption uses --form-string (not -F, which would exit 26 on a leading '<')"
    else
        fail "code-highlight e2e: sendDocument caption must use --form-string" "$(cat "$LOG_IMG2" 2>/dev/null)"
    fi
    if grep 'sendDocument' "$LOG_IMG2" | grep -qE -- '(^|[[:space:]])-F caption='; then
        fail "code-highlight e2e: sendDocument caption regressed to bare -F (curl exit-26 message-drop risk)" "$(cat "$LOG_IMG2" 2>/dev/null)"
    else
        ok "code-highlight e2e: sendDocument caption is NOT sent via bare -F"
    fi
else
    printf 'SKIP  code-highlight e2e: pygments not importable in this interpreter - skipping the html-doc happy-path checks (never-silent: this line IS the record)\n'
fi
rm -rf "$IMG2" "$STUB_IMG2" "$LOG_IMG2"

# -- [code_highlight] mode="off" -> identical to mode="inline-only" (no
# -- document), confirming the two non-"html-doc" values behave the same.
STUB_IMG3="$(mktemp -d)"; tts_essential_path "$STUB_IMG3" >/dev/null
LOG_IMG3="$(mktemp -u)"; write_stub_curl "$STUB_IMG3" "$LOG_IMG3"
IMG3="$(setup_format_bridge)"
cat > "$IMG3/relay.toml" <<'TOML'
[code_highlight]
mode = "off"
TOML
PATH="$STUB_IMG3" "$IMG3/tg-send.sh" '```myc
nodule example
```'
if ! grep -q 'sendDocument' "$LOG_IMG3" && grep -q 'language-rust' "$LOG_IMG3"; then
    ok "code-highlight e2e: mode=off sends no document (inline box still aliased, unaffected)"
else
    fail "code-highlight e2e: mode=off" "$(cat "$LOG_IMG3" 2>/dev/null)"
fi
rm -rf "$IMG3" "$STUB_IMG3" "$LOG_IMG3"

# -- an oversized block (over [code_highlight].max_lines) skips the
# -- document render - the inline box (unaffected) is the only thing sent.
STUB_IMG4="$(mktemp -d)"; tts_essential_path "$STUB_IMG4" >/dev/null
LOG_IMG4="$(mktemp -u)"; write_stub_curl "$STUB_IMG4" "$LOG_IMG4"
IMG4="$(setup_format_bridge)"
cat > "$IMG4/relay.toml" <<'TOML'
[code_highlight]
mode = "html-doc"
max_lines = 2
TOML
BIG_BLOCK="$(python3 -c "print(chr(10).join('line%d' % i for i in range(10)))" 2>/dev/null)"
PATH="$STUB_IMG4" "$IMG4/tg-send.sh" "\`\`\`python
${BIG_BLOCK:-line0
line1
line2}
\`\`\`"
if ! grep -q 'sendDocument' "$LOG_IMG4" && grep -q 'language-python' "$LOG_IMG4" \
    && grep -q "$(printf 'code_highlight\tfallback')" "$IMG4/.metrics.log" 2>/dev/null; then
    ok "code-highlight e2e: an oversized block (over max_lines) skips the document render, and the skip is logged"
else
    fail "code-highlight e2e: max_lines skip" "log=$(cat "$LOG_IMG4" 2>/dev/null) metrics=$(cat "$IMG4/.metrics.log" 2>/dev/null)"
fi
rm -rf "$IMG4" "$STUB_IMG4" "$LOG_IMG4"

# -- pygments unavailable (mocked import failure - see this file's header
# -- for why a stub python3 is used rather than actually uninstalling
# -- anything) -> mode=html-doc gracefully sends NO document; the inline
# -- box (unaffected either way) is the only thing that goes out.
STUB_IMG5="$(mktemp -d)"; tts_essential_path "$STUB_IMG5" >/dev/null
LOG_IMG5="$(mktemp -u)"; write_stub_curl "$STUB_IMG5" "$LOG_IMG5"
REAL_PYTHON3="$(command -v python3)"
# tts_essential_path already symlinked the REAL python3 at this path -
# remove the symlink FIRST so the heredoc below creates a fresh regular
# file, not a write THROUGH the symlink into the real system binary.
rm -f "$STUB_IMG5/python3"
cat > "$STUB_IMG5/python3" <<STUB
#!/bin/bash
case "\$*" in
    *"import pygments"*) exit 1 ;;
    *) exec "$REAL_PYTHON3" "\$@" ;;
esac
STUB
chmod +x "$STUB_IMG5/python3"
IMG5="$(setup_format_bridge)"
cat > "$IMG5/relay.toml" <<'TOML'
[code_highlight]
mode = "html-doc"
TOML
PATH="$STUB_IMG5" "$IMG5/tg-send.sh" '```myc
nodule example
```'
if ! grep -q 'sendDocument' "$LOG_IMG5" && grep -q 'language-rust' "$LOG_IMG5" \
    && grep -q "$(printf 'code_highlight\tfallback')" "$IMG5/.metrics.log" 2>/dev/null; then
    ok "code-highlight e2e: pygments unavailable -> no document sent, logged, inline box unaffected"
else
    fail "code-highlight e2e: pygments-absent fallback" "log=$(cat "$LOG_IMG5" 2>/dev/null) metrics=$(cat "$IMG5/.metrics.log" 2>/dev/null)"
fi
rm -rf "$IMG5" "$STUB_IMG5" "$LOG_IMG5"

# -- keep_text="none": the document sends with no caption at all.
STUB_IMG6="$(mktemp -d)"; tts_essential_path "$STUB_IMG6" >/dev/null
LOG_IMG6="$(mktemp -u)"; write_stub_curl "$STUB_IMG6" "$LOG_IMG6"
IMG6="$(setup_format_bridge)"
cat > "$IMG6/relay.toml" <<'TOML'
[code_highlight]
mode = "html-doc"
keep_text = "none"
TOML
PATH="$STUB_IMG6" "$IMG6/tg-send.sh" '```myc
nodule example
```'
if command -v python3 >/dev/null 2>&1 && python3 -c 'import pygments, pygments.formatters' >/dev/null 2>&1; then
    if grep -q 'sendDocument' "$LOG_IMG6" && ! grep 'sendDocument' "$LOG_IMG6" | grep -q 'caption='; then
        ok "code-highlight e2e: keep_text=none sends the document with no caption"
    else
        fail "code-highlight e2e: keep_text=none" "$(cat "$LOG_IMG6" 2>/dev/null)"
    fi
else
    printf 'SKIP  code-highlight e2e: keep_text=none - pygments not importable, skipping (never-silent: this line IS the record)\n'
fi
rm -rf "$IMG6" "$STUB_IMG6" "$LOG_IMG6"

# -- myc_inline_lang is configurable independent of [code_highlight].mode -
# -- confirms the alias applies even with mode="off" (see lib/format.sh).
STUB_IMG7="$(mktemp -d)"; tts_essential_path "$STUB_IMG7" >/dev/null
LOG_IMG7="$(mktemp -u)"; write_stub_curl "$STUB_IMG7" "$LOG_IMG7"
IMG7="$(setup_format_bridge)"
cat > "$IMG7/relay.toml" <<'TOML'
[code_highlight]
mode = "off"
myc_inline_lang = "mycelium"
TOML
PATH="$STUB_IMG7" "$IMG7/tg-send.sh" '```myc
nodule example
```'
if grep -q 'language-mycelium' "$LOG_IMG7" && ! grep -q 'language-rust' "$LOG_IMG7"; then
    ok "code-highlight e2e: myc_inline_lang applies independent of mode (mode=off, myc_inline_lang=mycelium -> literal tag)"
else
    fail "code-highlight e2e: myc_inline_lang independent of mode" "$(cat "$LOG_IMG7" 2>/dev/null)"
fi
rm -rf "$IMG7" "$STUB_IMG7" "$LOG_IMG7"

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

echo "== lib/usage_ingest.py + lib/dashboard_render.py: Python unit tests (opt-in token-usage aggregation, SYNTHETIC fixtures only) =="
if command -v python3 >/dev/null 2>&1; then
    PY_OUT="$(python3 "$REPO_ROOT/tests/test_usage_ingest.py" 2>&1)"
    PY_RC=$?
    if [[ $PY_RC -eq 0 ]]; then
        ok "python3 tests/test_usage_ingest.py"
    else
        fail "python3 tests/test_usage_ingest.py" "$PY_OUT"
    fi
else
    printf 'SKIP  python3 not installed - skipping usage-ingest unit tests (never-silent: this line IS the record)\n'
fi

echo "== lib/code_highlight.py: Python unit tests (pygments render + native MyceliumLexer) =="
if command -v python3 >/dev/null 2>&1; then
    PY_OUT="$(python3 "$REPO_ROOT/tests/test_code_highlight.py" 2>&1)"
    PY_RC=$?
    if [[ $PY_RC -eq 0 ]]; then
        ok "python3 tests/test_code_highlight.py"
    else
        fail "python3 tests/test_code_highlight.py" "$PY_OUT"
    fi
else
    printf 'SKIP  python3 not installed - skipping code-highlight unit tests (never-silent: this line IS the record)\n'
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

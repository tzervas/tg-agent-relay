#!/usr/bin/env python3
"""tests/test_providers_grok.py - Grok provider extension unit tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    global PASS, FAIL
    PASS = 0
    FAIL = 0
    sys.path.insert(0, str(REPO))
    from providers.base import get_provider, list_providers
    from providers.grok.hooks import EVENTS, format_hook, normalize_event

    PASS = FAIL = 0

    def ok(name: str) -> None:
        global PASS
        PASS += 1
        print(f"PASS  {name}")

    def fail(name: str, detail: str = "") -> None:
        global FAIL
        FAIL += 1
        print(f"FAIL  {name}")
        if detail:
            print(f"      {detail}")

    def eq(name: str, exp, act) -> None:
        if exp == act:
            ok(name)
        else:
            fail(name, f"expected {exp!r} got {act!r}")

    def contains(name: str, hay: str, *needles: str) -> None:
        missing = [n for n in needles if n not in hay]
        if missing:
            fail(name, f"missing {missing!r} in {hay!r}")
        else:
            ok(name)

    # --- registry ---
    ids = {p.id for p in list_providers()}
    eq("registry has grok", True, "grok" in ids)
    eq("registry has claude", True, "claude" in ids)
    eq("registry has ollama", True, "ollama" in ids)
    g = get_provider("grok")
    assert g is not None
    eq("grok has 14 hook events", 14, len(g.hook_events))
    eq("EVENTS catalog size", 14, len(EVENTS))
    eq("grok usage_source", "grok", g.usage_source)
    eq("grok provider_label", "xai", g.provider_label)
    eq("grok backend_id", "grok", g.backend_id)
    eq("register provider has format_hook", True, g.format_hook is not None)
    eq("format_hook is hooks.format_hook", format_hook, g.format_hook)

    # --- normalize: Pascal / snake / Cursor aliases ---
    for raw, want in [
        ("Stop", "Stop"),
        ("stop", "Stop"),
        ("pre_tool_use", "PreToolUse"),
        ("preToolUse", "PreToolUse"),
        ("beforeShellExecution", "PreToolUse"),
        ("beforeMcpExecution", "PreToolUse"),
        ("beforeReadFile", "PreToolUse"),
        ("afterShellExecution", "PostToolUse"),
        ("afterMcpExecution", "PostToolUse"),
        ("afterFileEdit", "PostToolUse"),
        ("afterAgentResponse", "PostToolUse"),
        ("SubagentEnd", "SubagentStop"),
        ("subagent_end", "SubagentStop"),
        ("subagentEnd", "SubagentStop"),
        ("session_start", "SessionStart"),
        ("SessionStart", "SessionStart"),
        ("user_prompt_submit", "UserPromptSubmit"),
        ("beforeSubmitPrompt", "UserPromptSubmit"),
        ("post_tool_use_failure", "PostToolUseFailure"),
        ("permission_denied", "PermissionDenied"),
        ("stop_failure", "StopFailure"),
        ("notification", "Notification"),
        ("subagent_start", "SubagentStart"),
        ("pre_compact", "PreCompact"),
        ("post_compact", "PostCompact"),
        ("session_end", "SessionEnd"),
    ]:
        eq(f"normalize {raw}", want, normalize_event(raw))
    eq("normalize empty", "unknown", normalize_event(""))
    eq("normalize None-ish", "unknown", normalize_event(None))  # type: ignore[arg-type]

    # --- each event: non-empty with prefix (sparse payload) ---
    for e in g.hook_events:
        summary = format_hook(
            {"toolName": "run_terminal_command", "message": "hi"},
            e.name,
            {"prefix": e.default_prefix},
        )
        if summary and e.default_prefix in summary and "\n" not in summary:
            ok(f"format {e.name}")
        else:
            fail(f"format {e.name}", repr(summary))

    # --- missing fields: empty payload still one-liner with prefix ---
    for e in EVENTS:
        empty = format_hook({}, e.name, {"prefix": e.default_prefix})
        if empty and e.default_prefix in empty and empty.strip() and "\n" not in empty:
            ok(f"missing fields {e.name}")
        else:
            fail(f"missing fields {e.name}", repr(empty))

    # --- custom format templates ---
    custom = format_hook(
        {"toolName": "Edit", "tool_name": "Edit"},
        "PreToolUse",
        {"prefix": "🔧", "format": "{prefix} CUSTOM {tool} ({event})"},
    )
    eq("custom format template", "🔧 CUSTOM Edit (PreToolUse)", custom)

    custom_stop = format_hook(
        {"last_assistant_message": "done", "stop_reason": "end_turn"},
        "Stop",
        {"prefix": "🏁", "format": "{prefix} [{stop_reason}] {message}"},
    )
    eq("custom Stop format", "🏁 [end_turn] done", custom_stop)

    # --- Stop / SubagentStop prefer last_assistant_message ---
    stop = format_hook(
        {
            "last_assistant_message": "ship it\nnow",
            "message": "fallback",
            "stop_reason": "end_turn",
        },
        "Stop",
        {"prefix": "🏁"},
    )
    contains("Stop prefers last_assistant_message", stop, "🏁", "ship it now")
    if "fallback" in stop:
        fail("Stop ignores secondary message", repr(stop))
    else:
        ok("Stop ignores secondary message")
    if "\n" in stop:
        fail("Stop oneline", repr(stop))
    else:
        ok("Stop oneline")

    # stop_reason alone must not become junk " — end_turn" detail
    stop_reason_only = format_hook(
        {"stop_reason": "end_turn"},
        "Stop",
        {"prefix": "🏁"},
    )
    eq("Stop without message no junk reason", "🏁 Grok turn finished", stop_reason_only)

    # message alone still works (fixture convention)
    stop_msg = format_hook({"message": "all green"}, "Stop", {"prefix": "🏁"})
    contains("Stop message fallback", stop_msg, "all green")

    sub = format_hook(
        {
            "agent_type": "explore",
            "last_assistant_message": "found 3 files\nwith matches",
            "message": "ignored when last set",
        },
        "SubagentStop",
        {"prefix": "✅"},
    )
    contains("SubagentStop last_assistant", sub, "✅", "explore", "found 3 files")
    if "\n" in sub:
        fail("SubagentStop oneline", repr(sub))
    else:
        ok("SubagentStop oneline")

    # --- PostToolUseFailure surfaces error ---
    fail_sum = format_hook(
        {
            "toolName": "run_terminal_command",
            "error": "command exited with code 1",
        },
        "PostToolUseFailure",
        {"prefix": "⚠️"},
    )
    contains(
        "PostToolUseFailure error",
        fail_sum,
        "⚠️",
        "run_terminal_command",
        "command exited with code 1",
    )

    # --- Pre/PostToolUse include tool name + optional truncated input ---
    pre = format_hook(
        {
            "toolName": "run_terminal_command",
            "toolInput": {"command": "ls"},
        },
        "PreToolUse",
        {"prefix": "🔧"},
    )
    contains("PreToolUse tool+input", pre, "🔧", "using", "run_terminal_command", "ls")

    post = format_hook(
        {"tool_name": "Write", "tool_input": {"path": "a.py"}},
        "PostToolUse",
        {"prefix": "🔧"},
    )
    contains("PostToolUse tool name", post, "used", "Write")

    long_cmd = "x" * 400
    pre_long = format_hook(
        {"toolName": "Bash", "toolInput": {"command": long_cmd}},
        "PreToolUse",
        {"prefix": "🔧", "tool_input_limit": "40"},
    )
    if "..." in pre_long and "Bash" in pre_long and "\n" not in pre_long:
        ok("PreToolUse tool_input_limit truncation")
    else:
        fail("PreToolUse tool_input_limit truncation", repr(pre_long))

    # --- PermissionDenied reason ---
    denied = format_hook(
        {
            "toolName": "run_terminal_command",
            "reason": "dangerous command blocked by policy",
        },
        "PermissionDenied",
        {"prefix": "🚫"},
    )
    contains("PermissionDenied reason", denied, "🚫", "run_terminal_command", "dangerous")

    # --- Notification fields ---
    notif = format_hook(
        {"notification_type": "idle_prompt", "message": "Waiting for input"},
        "Notification",
        {"prefix": "🔔"},
    )
    contains("Notification fields", notif, "🔔", "idle_prompt", "Waiting for input")

    # --- StopFailure error_type ---
    sf = format_hook(
        {"error_type": "api_error", "message": "upstream failed"},
        "StopFailure",
        {"prefix": "🛑"},
    )
    contains("StopFailure error_type", sf, "🛑", "api_error")

    # --- whitespace collapse ---
    multi = format_hook(
        {"prompt": "line1\n\nline2\t  line3"},
        "UserPromptSubmit",
        {"prefix": "⌨️"},
    )
    if "\n" in multi or "\t" in multi:
        fail("collapse whitespace", repr(multi))
    else:
        contains("collapse whitespace", multi, "line1", "line2", "line3")

    # --- placeholders declared for catalog events ---
    for e in EVENTS:
        if "prefix" in e.placeholders and "event" in e.placeholders:
            ok(f"placeholders base {e.name}")
        else:
            fail(f"placeholders base {e.name}", repr(e.placeholders))

    # --- fixtures ---
    _fixtures = REPO / "tests" / "fixtures" / "hooks" / "grok"
    if _fixtures.is_dir():
        for path in sorted(_fixtures.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                fail(f"fixture load {path.name}", str(exc))
                continue
            raw_ev = data.get("hookEventName") or data.get("hook_event_name") or path.stem
            ev = normalize_event(str(raw_ev))
            pref = next((e.default_prefix for e in EVENTS if e.name == ev), "ℹ️")
            summary = format_hook(data, ev, {"prefix": pref})
            if summary and pref in summary and "\n" not in summary:
                ok(f"fixture format {path.name}")
            else:
                fail(f"fixture format {path.name}", repr(summary))
    else:
        fail("fixtures dir present", str(_fixtures))

    # --- default-on lifecycle ---
    on = {e.name for e in g.hook_events if e.default_enabled}
    eq(
        "default-on lifecycle set",
        {"Stop", "StopFailure", "SubagentStop", "Notification", "PostToolUseFailure"},
        on,
    )

    # --- provider_hook CLI ---
    proc = subprocess.run(
        [sys.executable, str(REPO / "lib" / "provider_hook.py"), "grok"],
        input=json.dumps({"hookEventName": "stop", "message": "all green"}),
        text=True,
        capture_output=True,
        cwd=str(REPO),
        timeout=10,
    )
    eq("provider_hook CLI exit 0", 0, proc.returncode)
    eq("provider_hook OK prefix", True, proc.stdout.startswith("OK:"))
    eq("provider_hook contains message", True, "all green" in proc.stdout)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"grok": {"Stop": {"enabled": False}}}, f)
        cfg = f.name
    proc2 = subprocess.run(
        [sys.executable, str(REPO / "lib" / "provider_hook.py"), "grok", "--config-json", cfg],
        input=json.dumps({"hookEventName": "stop"}),
        text=True,
        capture_output=True,
        cwd=str(REPO),
        timeout=10,
    )
    eq("provider_hook respects enabled=false", True, proc2.stdout.startswith("SKIP:disabled"))

    # custom format via config-json
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "grok": {
                    "PreToolUse": {
                        "enabled": True,
                        "prefix": "🔧",
                        "format": "{prefix} CFG {tool}",
                    }
                }
            },
            f,
        )
        cfg_fmt = f.name
    proc3 = subprocess.run(
        [
            sys.executable,
            str(REPO / "lib" / "provider_hook.py"),
            "grok",
            "--config-json",
            cfg_fmt,
        ],
        input=json.dumps({"hookEventName": "pre_tool_use", "toolName": "run_terminal_command"}),
        text=True,
        capture_output=True,
        cwd=str(REPO),
        timeout=10,
    )
    eq("provider_hook custom format exit", 0, proc3.returncode)
    eq(
        "provider_hook custom format body",
        True,
        "OK:🔧 CFG run_terminal_command" in proc3.stdout,
    )

    cat = subprocess.run(
        [
            sys.executable,
            str(REPO / "lib" / "provider_catalog.py"),
            "events",
            "grok",
            "--names-only",
        ],
        text=True,
        capture_output=True,
        cwd=str(REPO),
        timeout=10,
    )
    names = [ln for ln in cat.stdout.splitlines() if ln.strip()]
    eq("catalog lists 14 events", 14, len(names))
    eq("catalog includes SessionEnd", True, "SessionEnd" in names)

    print()
    print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
    return 0 if FAIL == 0 else 1


def test_providers_grok() -> None:
    """pytest entry — dual-runs the standalone script checks."""
    rc = main()
    assert rc == 0, f"script-style checks failed (exit {rc}); see PASS/FAIL lines above"


if __name__ == "__main__":
    raise SystemExit(main())

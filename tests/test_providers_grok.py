#!/usr/bin/env python3
"""tests/test_providers_grok.py - Grok provider extension unit tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    global PASS, FAIL
    PASS = 0
    FAIL = 0
    sys.path.insert(0, str(REPO))
    from providers.base import get_provider, list_providers
    from providers.grok.hooks import format_hook, normalize_event

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

    ids = {p.id for p in list_providers()}
    eq("registry has grok", True, "grok" in ids)
    eq("registry has claude", True, "claude" in ids)
    eq("registry has ollama", True, "ollama" in ids)
    g = get_provider("grok")
    assert g is not None
    eq("grok has 14 hook events", 14, len(g.hook_events))
    eq("grok usage_source", "grok", g.usage_source)
    eq("grok provider_label", "xai", g.provider_label)
    eq("grok backend_id", "grok", g.backend_id)
    for raw, want in [
        ("Stop", "Stop"),
        ("stop", "Stop"),
        ("pre_tool_use", "PreToolUse"),
        ("preToolUse", "PreToolUse"),
        ("beforeShellExecution", "PreToolUse"),
        ("SubagentEnd", "SubagentStop"),
        ("session_start", "SessionStart"),
        ("afterFileEdit", "PostToolUse"),
    ]:
        eq(f"normalize {raw}", want, normalize_event(raw))
    for e in g.hook_events:
        summary = format_hook(
            {"toolName": "run_terminal_command", "message": "hi"},
            e.name,
            {"prefix": e.default_prefix},
        )
        if summary and e.default_prefix in summary:
            ok(f"format {e.name}")
        else:
            fail(f"format {e.name}", repr(summary))
    on = {e.name for e in g.hook_events if e.default_enabled}
    eq(
        "default-on lifecycle set",
        {"Stop", "StopFailure", "SubagentStop", "Notification", "PostToolUseFailure"},
        on,
    )
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
    import tempfile

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

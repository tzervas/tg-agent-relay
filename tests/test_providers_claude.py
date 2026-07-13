#!/usr/bin/env python3
"""tests/test_providers_claude.py - Claude Code provider extension unit tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import providers  # noqa: F401
from providers.base import get_provider, list_providers
from providers.claude.hooks import EVENTS, format_hook, normalize_event

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


# Registry
ids = {p.id for p in list_providers()}
eq("registry has claude", True, "claude" in ids)

c = get_provider("claude")
assert c is not None
eq("claude has 30 hook events", 30, len(c.hook_events))
eq("claude usage_source", "claude-code", c.usage_source)
eq("claude provider_label", "anthropic", c.provider_label)
eq("claude backend_id", "claude", c.backend_id)
eq("register provider has format_hook", True, c.format_hook is not None)
eq("format_hook is hooks.format_hook", format_hook, c.format_hook)

# Normalize
for raw, want in [
    ("Stop", "Stop"),
    ("stop", "Stop"),
    ("subagent_stop", "SubagentStop"),
    ("SubagentStop", "SubagentStop"),
    ("pre_tool_use", "PreToolUse"),
    ("notification", "Notification"),
    ("session_start", "SessionStart"),
    ("UserPromptSubmit", "UserPromptSubmit"),
    ("TotallyNewHook", "TotallyNewHook"),  # unknown passthrough
]:
    eq(f"normalize {raw}", want, normalize_event(raw))

eq("normalize empty", "unknown", normalize_event(""))
eq("normalize None-ish", "unknown", normalize_event(None))  # type: ignore[arg-type]

# All 30 events produce non-empty summary with default prefix
for e in EVENTS:
    summary = format_hook(
        {"tool_name": "Bash", "message": "hi", "agent_type": "Explore"},
        e.name,
        {"prefix": e.default_prefix},
    )
    if summary and e.default_prefix in summary:
        ok(f"format {e.name}")
    else:
        fail(f"format {e.name}", repr(summary))

# Notification with notification_type/message
notif = format_hook(
    {"notification_type": "idle_prompt", "message": "Claude is waiting"},
    "Notification",
    {"prefix": "🔔"},
)
if "🔔" in notif and "idle_prompt" in notif and "Claude is waiting" in notif:
    ok("Notification fields")
else:
    fail("Notification fields", repr(notif))

# SubagentStop with agent_type and last_assistant_message
sub = format_hook(
    {
        "agent_type": "Explore",
        "last_assistant_message": "found 3 files\nwith matches",
    },
    "SubagentStop",
    {"prefix": "✅"},
)
if (
    "✅" in sub
    and "Explore" in sub
    and "found 3 files" in sub
    and "\n" not in sub  # oneline collapse
):
    ok("SubagentStop fields + oneline")
else:
    fail("SubagentStop fields + oneline", repr(sub))

# custom format template honored
custom = format_hook(
    {"tool_name": "Edit", "source": "startup"},
    "PreToolUse",
    {"prefix": "🔧", "format": "{prefix} CUSTOM {tool} ({event})"},
)
eq("custom format template", "🔧 CUSTOM Edit (PreToolUse)", custom)

# Fixture samples (when present) round-trip through format_hook
_fixtures = REPO / "tests" / "fixtures" / "hooks" / "claude"
if _fixtures.is_dir():
    for path in sorted(_fixtures.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"fixture load {path.name}", str(exc))
            continue
        ev = normalize_event(str(data.get("hook_event_name") or path.stem))
        pref = next((e.default_prefix for e in EVENTS if e.name == ev), "ℹ️")
        summary = format_hook(data, ev, {"prefix": pref})
        if summary and pref in summary:
            ok(f"fixture format {path.name}")
        else:
            fail(f"fixture format {path.name}", repr(summary))
else:
    fail("fixtures dir present", str(_fixtures))

# Default-on set (matches install defaults / shell adapter)
on = {e.name for e in EVENTS if e.default_enabled}
eq(
    "default-on lifecycle set",
    {"Stop", "StopFailure", "SubagentStop", "Notification", "PostToolUseFailure"},
    on,
)

# CLI provider_hook
proc = subprocess.run(
    [sys.executable, str(REPO / "lib" / "provider_hook.py"), "claude"],
    input=json.dumps(
        {
            "hook_event_name": "Notification",
            "notification_type": "permission_prompt",
            "message": "allow shell?",
        }
    ),
    text=True,
    capture_output=True,
    cwd=str(REPO),
    timeout=10,
)
eq("provider_hook CLI exit 0", 0, proc.returncode)
eq("provider_hook OK prefix", True, proc.stdout.startswith("OK:"))
eq("provider_hook contains message", True, "allow shell?" in proc.stdout)

# disabled via config
import tempfile

with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
    json.dump({"claude_code": {"Notification": {"enabled": False}}}, f)
    cfg = f.name
proc2 = subprocess.run(
    [
        sys.executable,
        str(REPO / "lib" / "provider_hook.py"),
        "claude",
        "--config-json",
        cfg,
    ],
    input=json.dumps({"hook_event_name": "Notification"}),
    text=True,
    capture_output=True,
    cwd=str(REPO),
    timeout=10,
)
eq("provider_hook respects enabled=false", True, proc2.stdout.startswith("SKIP:disabled"))

# catalog
cat = subprocess.run(
    [
        sys.executable,
        str(REPO / "lib" / "provider_catalog.py"),
        "events",
        "claude",
        "--names-only",
    ],
    text=True,
    capture_output=True,
    cwd=str(REPO),
    timeout=10,
)
names = [ln for ln in cat.stdout.splitlines() if ln.strip()]
eq("catalog lists 30 events", 30, len(names))
eq("catalog includes SessionEnd", True, "SessionEnd" in names)
eq("catalog includes ElicitationResult", True, "ElicitationResult" in names)

print()
print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)

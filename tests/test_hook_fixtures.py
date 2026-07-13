#!/usr/bin/env python3
"""tests/test_hook_fixtures.py - Offline tests for synthetic hook JSON fixtures.

Loads every Grok fixture under tests/fixtures/hooks/grok/ and exercises
tg_agent_relay.hooks.dispatch_hook / providers.grok.hooks.format_hook.
Claude fixtures are validated as parseable JSON with expected fields.

NO network, NO live harness. Run:

  source lib/python.sh && relay_python tests/test_hook_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import providers  # noqa: F401
from providers.base import get_provider
from providers.grok.hooks import EVENTS, format_hook, normalize_event
from tg_agent_relay.hooks import dispatch_hook

FIXTURES = REPO / "tests" / "fixtures" / "hooks"
GROK_DIR = FIXTURES / "grok"
CLAUDE_DIR = FIXTURES / "claude"

PASS = 0
FAIL = 0
FAILURES: list[str] = []

DEFAULT_ENABLED = {e.name for e in EVENTS if e.default_enabled}
# Stop, StopFailure, SubagentStop, Notification, PostToolUseFailure
assert {
    "Stop",
    "StopFailure",
    "SubagentStop",
    "Notification",
    "PostToolUseFailure",
} == DEFAULT_ENABLED

EXPECTED_GROK_FILES = {f"{e.name}.json" for e in EVENTS}


def ok(name: str) -> None:
    global PASS
    PASS += 1
    print(f"PASS  {name}")


def fail(name: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    FAILURES.append(name)
    print(f"FAIL  {name}")
    if detail:
        print(f"      {detail}")


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise TypeError(f"{path}: expected object, got {type(data)}")
    return data


def main() -> int:
    # --- inventory ---
    if not GROK_DIR.is_dir():
        fail("grok fixture dir exists", str(GROK_DIR))
        return 1
    grok_files = sorted(p.name for p in GROK_DIR.glob("*.json"))
    if set(grok_files) == EXPECTED_GROK_FILES:
        ok(f"grok has all 14 event fixtures ({len(grok_files)})")
    else:
        missing = EXPECTED_GROK_FILES - set(grok_files)
        extra = set(grok_files) - EXPECTED_GROK_FILES
        fail(
            "grok has all 14 event fixtures",
            f"missing={sorted(missing)} extra={sorted(extra)}",
        )

    provider = get_provider("grok")
    if provider is None or provider.format_hook is None:
        fail("grok provider registered with format_hook")
        return 1
    ok("grok provider registered with format_hook")

    # --- each Grok fixture ---
    for name in sorted(EXPECTED_GROK_FILES):
        path = GROK_DIR / name
        event_name = path.stem  # PascalCase file name == canonical event
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            fail(f"load {name}", str(exc))
            continue
        ok(f"load {name}")

        raw = payload.get("hookEventName") or payload.get("hook_event_name") or ""
        norm = normalize_event(str(raw))
        if norm == event_name:
            ok(f"normalize {name} → {norm}")
        else:
            fail(f"normalize {name}", f"raw={raw!r} got {norm!r} want {event_name!r}")

        # format_hook always produces a non-empty summary (independent of enabled)
        summary = format_hook(payload, event_name, {"prefix": provider.default_prefix(event_name)})
        if summary and summary.strip():
            ok(f"format_hook non-empty {name}")
        else:
            fail(f"format_hook non-empty {name}", repr(summary))

        if "Claude Code" in summary:
            fail(f"no Claude Code in grok summary {name}", summary)
        else:
            ok(f"no Claude Code in grok summary {name}")

        # dispatch_hook respects default enablement
        status, body = dispatch_hook("grok", payload)
        if event_name in DEFAULT_ENABLED:
            if status == "OK" and body and body.strip():
                ok(f"dispatch default-on OK {name}")
            else:
                fail(f"dispatch default-on OK {name}", f"{status!r} {body!r}")
            if "Claude Code" in body:
                fail(f"dispatch body no Claude Code {name}", body)
            else:
                ok(f"dispatch body no Claude Code {name}")
        else:
            # default-disabled: SKIP:disabled OR OK when explicitly enabled
            if status == "SKIP" and str(body).startswith("disabled:"):
                ok(f"dispatch default-off SKIP {name}")
            elif status == "OK" and body:
                ok(f"dispatch default-off still OK {name}")
            else:
                fail(f"dispatch default-off SKIP or OK {name}", f"{status!r} {body!r}")

            # With enabled override, format must work (OK + non-empty)
            cfg = {"grok": {event_name: {"enabled": True}}}
            st2, body2 = dispatch_hook("grok", payload, config=cfg)
            if st2 == "OK" and body2 and body2.strip():
                ok(f"dispatch enabled-override OK {name}")
            else:
                fail(f"dispatch enabled-override OK {name}", f"{st2!r} {body2!r}")
            if "Claude Code" in (body2 or ""):
                fail(f"override body no Claude Code {name}", body2)
            else:
                ok(f"override body no Claude Code {name}")

    # PreToolUse fixture shape (task conventions)
    pre = load_json(GROK_DIR / "PreToolUse.json")
    if pre.get("toolName") == "run_terminal_command":
        ok("PreToolUse toolName")
    else:
        fail("PreToolUse toolName", repr(pre.get("toolName")))
    tin = pre.get("toolInput") or {}
    if isinstance(tin, dict) and tin.get("command") == "ls":
        ok("PreToolUse toolInput.command")
    else:
        fail("PreToolUse toolInput.command", repr(tin))
    if pre.get("toolUseId") == "call_abc123xyz999":
        ok("PreToolUse toolUseId")
    else:
        fail("PreToolUse toolUseId", repr(pre.get("toolUseId")))

    stop = load_json(GROK_DIR / "Stop.json")
    if stop.get("hookEventName") == "stop" and stop.get("message") == "all green":
        ok("Stop fixture convention")
    else:
        fail("Stop fixture convention", repr(stop))

    # SessionEnd uses PascalCase hookEventName (one Pascal sample among snakes)
    send = load_json(GROK_DIR / "SessionEnd.json")
    if send.get("hookEventName") == "SessionEnd":
        ok("SessionEnd PascalCase hookEventName sample")
    else:
        fail("SessionEnd PascalCase hookEventName sample", repr(send.get("hookEventName")))

    # --- Claude fixtures (shape only; shell adapter is authoritative) ---
    required_claude = {
        "SessionStart.json",
        "Notification.json",
        "SubagentStop.json",
        "Stop.json",
        "PreToolUse.json",
        "PostToolUseFailure.json",
    }
    if not CLAUDE_DIR.is_dir():
        fail("claude fixture dir exists", str(CLAUDE_DIR))
    else:
        present = {p.name for p in CLAUDE_DIR.glob("*.json")}
        if required_claude <= present:
            ok(f"claude required fixtures present ({len(required_claude)})")
        else:
            fail("claude required fixtures present", f"missing={sorted(required_claude - present)}")

        field_checks = {
            "SessionStart.json": ("hook_event_name", "SessionStart"),
            "Notification.json": ("notification_type", None),
            "SubagentStop.json": ("agent_type", None),
            "Stop.json": ("last_assistant_message", None),
            "PreToolUse.json": ("tool_name", None),
            "PostToolUseFailure.json": ("tool_name", None),
        }
        for fname, (field, want_event) in field_checks.items():
            path = CLAUDE_DIR / fname
            if not path.is_file():
                continue
            try:
                data = load_json(path)
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                fail(f"claude load {fname}", str(exc))
                continue
            he = data.get("hook_event_name")
            if want_event is not None:
                if he == want_event:
                    ok(f"claude {fname} hook_event_name")
                else:
                    fail(f"claude {fname} hook_event_name", repr(he))
            elif he and he[0].isupper():
                ok(f"claude {fname} PascalCase hook_event_name")
            else:
                fail(f"claude {fname} PascalCase hook_event_name", repr(he))
            if field in data and data[field] not in (None, ""):
                ok(f"claude {fname} has {field}")
            else:
                fail(f"claude {fname} has {field}", repr(data.get(field)))

    print()
    print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
    if FAILURES:
        print("Failed:", ", ".join(FAILURES))
    return 0 if FAIL == 0 else 1


def test_hook_fixtures() -> None:
    """pytest entry — dual-runs the standalone script checks."""
    rc = main()
    assert rc == 0, f"script-style checks failed (exit {rc}); see PASS/FAIL lines above"


if __name__ == "__main__":
    raise SystemExit(main())

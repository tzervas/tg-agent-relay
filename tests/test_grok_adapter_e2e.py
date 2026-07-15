#!/usr/bin/env python3
"""tests/test_grok_adapter_e2e.py - Offline Grok adapter e2e (issue #63).

Covers adapters/grok.sh + hook-notify.sh smart dispatch with a mock
tg-send that records messages to disk. No network, no live harness.

Checks:
  - default-on fixture events produce a recorded send
  - disabled Stop via relay.toml produces empty record
  - custom prefix via [grok.Notification] in relay.toml
  - Cursor alias payload (preToolUse) matches PreToolUse formatting
  - hook-notify treats Grok payloads as Grok (not Claude unknown)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import providers  # noqa: F401
from providers.base import get_provider
from providers.grok.hooks import EVENTS, format_hook, normalize_event

FIXTURES = REPO / "tests" / "fixtures" / "hooks" / "grok"
DEFAULT_ON = {e.name for e in EVENTS if e.default_enabled}
EXPECTED_ON = {
    "Stop",
    "StopFailure",
    "SubagentStop",
    "Notification",
    "PostToolUseFailure",
}

PASS = 0
FAIL = 0
FAILURES: list[str] = []

# Mock tg-send must not hit the network; force shell send path when present.
_E2E_ENV = {
    **os.environ,
    "RELAY_PYTHON_SEND": "0",
    "RELAY_PYTHON_POLL": "0",
    "RELAY_PYTHON_FALLBACK_QUIET": "1",
}


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


def eq(name: str, expected: str, actual: str) -> None:
    if expected == actual:
        ok(name)
    else:
        fail(name, f"expected {expected!r} got {actual!r}")


def empty(name: str, actual: str) -> None:
    if actual == "":
        ok(name)
    else:
        fail(name, f"expected empty, got {actual!r}")


def _symlink(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    os.symlink(src, dst)


def setup_temp_bridge() -> Path:
    """Throwaway bridge: real scripts/libs symlinked, mock tg-send records."""
    d = Path(tempfile.mkdtemp(prefix="grok-e2e-"))
    for name in (
        "hook-notify.sh",
        "hook-notify-grok.sh",
        "relay-notify.sh",
    ):
        _symlink(REPO / name, d / name)
    (d / "adapters").mkdir()
    (d / "lib").mkdir()
    _symlink(REPO / "adapters" / "grok.sh", d / "adapters" / "grok.sh")
    _symlink(REPO / "adapters" / "claude-code.sh", d / "adapters" / "claude-code.sh")
    for name in (
        "relay-config.sh",
        "relay-common.sh",
        "claude-code-events.sh",
        "grok-events.sh",
        "routing.sh",
        "provider_hook.py",
        "provider_catalog.py",
        "python.sh",
        "toml_to_json.py",
        "format.sh",
        "tts.sh",
        "code_highlight.sh",
        "code_highlight.py",
        "python_fallback.sh",
    ):
        src = REPO / "lib" / name
        if src.exists():
            _symlink(src, d / "lib" / name)
    _symlink(REPO / "providers", d / "providers")

    mock = d / "tg-send.sh"
    mock.write_text(
        "#!/bin/bash\n"
        "set -u\n"
        'if [[ $# -gt 0 ]]; then MSG="$*"; else MSG="$(cat)"; fi\n'
        'd="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
        'printf \'%s\' "$MSG" > "$d/.recorded"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    mock.chmod(0o755)
    return d


def recorded(bridge: Path) -> str:
    path = bridge / ".recorded"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def clear_recorded(bridge: Path) -> None:
    path = bridge / ".recorded"
    if path.is_file():
        path.unlink()


def run_adapter(bridge: Path, payload: str | bytes | dict, entry: str = "adapters/grok.sh") -> str:
    """Pipe payload into adapter or hook-notify; return recorded send body."""
    clear_recorded(bridge)
    if isinstance(payload, dict):
        raw = json.dumps(payload, ensure_ascii=False)
    elif isinstance(payload, bytes):
        raw = payload.decode("utf-8")
    else:
        raw = payload
    script = bridge / entry
    subprocess.run(
        ["bash", str(script)],
        input=raw,
        text=True,
        capture_output=True,
        cwd=str(bridge),
        env=_E2E_ENV,
        timeout=30,
        check=False,
    )
    return recorded(bridge)


def load_fixture(name: str) -> dict:
    path = FIXTURES / name
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{name}: expected object")
    return data


def expected_summary(payload: dict, event: str | None = None) -> str:
    provider = get_provider("grok")
    assert provider is not None
    raw = event or payload.get("hookEventName") or payload.get("hook_event_name") or ""
    norm = normalize_event(str(raw))
    prefix = provider.default_prefix(norm)
    return format_hook(payload, norm, {"prefix": prefix})


def main() -> int:
    global PASS, FAIL
    PASS = FAIL = 0
    FAILURES.clear()

    if set(DEFAULT_ON) != EXPECTED_ON:
        fail("default-on lifecycle set", f"got {sorted(DEFAULT_ON)}")
    else:
        ok("default-on lifecycle set")

    # --- inventory: 14 fixtures with event-shaped fields ---
    names = sorted(p.name for p in FIXTURES.glob("*.json"))
    want_files = {f"{e.name}.json" for e in EVENTS}
    if set(names) == want_files and len(names) == 14:
        ok(f"fixture inventory ({len(names)} files)")
    else:
        fail(
            "fixture inventory",
            f"got={names} missing={sorted(want_files - set(names))} "
            f"extra={sorted(set(names) - want_files)}",
        )

    # Per-event field sanity (realistic payload shapes)
    field_checks: dict[str, tuple[str, ...]] = {
        "SessionStart.json": ("sessionId", "cwd", "workspaceRoot"),
        "UserPromptSubmit.json": ("prompt", "sessionId"),
        "PreToolUse.json": ("toolName", "toolInput", "toolUseId"),
        "PostToolUse.json": ("toolName", "toolInput", "toolUseId"),
        "PostToolUseFailure.json": ("toolName", "error", "toolUseId"),
        "PermissionDenied.json": ("toolName", "reason"),
        "Stop.json": ("message", "last_assistant_message", "stop_reason"),
        "StopFailure.json": ("error_type", "message"),
        "Notification.json": ("notification_type", "message"),
        "SubagentStart.json": ("agent_type", "agent_id"),
        "SubagentStop.json": ("agent_type", "last_assistant_message"),
        "PreCompact.json": ("trigger",),
        "PostCompact.json": ("trigger",),
        "SessionEnd.json": ("reason",),
    }
    for fname, keys in field_checks.items():
        try:
            data = load_fixture(fname)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            fail(f"fixture load {fname}", str(exc))
            continue
        missing = [k for k in keys if not data.get(k)]
        if missing:
            fail(f"fixture fields {fname}", f"missing {missing}")
        else:
            ok(f"fixture fields {fname}")

    # Conventions preserved for other suites
    pre = load_fixture("PreToolUse.json")
    if (
        pre.get("toolName") == "run_terminal_command"
        and isinstance(pre.get("toolInput"), dict)
        and pre["toolInput"].get("command") == "ls"
        and pre.get("toolUseId") == "call_abc123xyz999"
    ):
        ok("PreToolUse fixture conventions")
    else:
        fail("PreToolUse fixture conventions", repr(pre))

    stop = load_fixture("Stop.json")
    if stop.get("hookEventName") == "stop" and stop.get("message") == "all green":
        ok("Stop fixture conventions")
    else:
        fail("Stop fixture conventions", repr(stop))

    send = load_fixture("SessionEnd.json")
    if send.get("hookEventName") == "SessionEnd":
        ok("SessionEnd PascalCase hookEventName sample")
    else:
        fail("SessionEnd PascalCase hookEventName sample", repr(send.get("hookEventName")))

    # --- adapter e2e: default-on fixtures produce recorded send ---
    bridge = setup_temp_bridge()
    try:
        for event_name in sorted(EXPECTED_ON):
            fname = f"{event_name}.json"
            payload = load_fixture(fname)
            rec = run_adapter(bridge, payload)
            want = expected_summary(payload, event_name)
            if rec == want and rec and "Claude Code" not in rec:
                ok(f"adapter default-on {event_name}")
            else:
                fail(
                    f"adapter default-on {event_name}",
                    f"want={want!r} got={rec!r}",
                )

        # Default-off event should not send without override
        pre_payload = load_fixture("PreToolUse.json")
        rec_off = run_adapter(bridge, pre_payload)
        empty("adapter default-off PreToolUse no send", rec_off)

        # --- disabled Stop produces empty ---
        (bridge / "relay.toml").write_text(
            "[grok.Stop]\nenabled = false\n",
            encoding="utf-8",
        )
        rec_dis = run_adapter(bridge, load_fixture("Stop.json"))
        empty("adapter disabled Stop no send", rec_dis)

        # --- custom prefix via [grok.Notification] ---
        (bridge / "relay.toml").write_text(
            '[grok.Notification]\nprefix = "⭐"\n',
            encoding="utf-8",
        )
        notif = load_fixture("Notification.json")
        rec_pref = run_adapter(bridge, notif)
        # format_hook with custom prefix
        want_pref = format_hook(
            notif,
            "Notification",
            {"prefix": "⭐"},
        )
        eq("adapter custom Notification prefix", want_pref, rec_pref)
        if rec_pref.startswith("⭐") and "idle_prompt" in rec_pref:
            ok("adapter custom prefix body shape")
        else:
            fail("adapter custom prefix body shape", repr(rec_pref))

        # Reset config for remaining tests
        (bridge / "relay.toml").unlink(missing_ok=True)

        # --- Cursor alias: preToolUse → same formatter as PreToolUse ---
        (bridge / "relay.toml").write_text(
            "[grok.PreToolUse]\nenabled = true\n",
            encoding="utf-8",
        )
        alias_payload = {
            "hookEventName": "preToolUse",
            "sessionId": "sess_cursor_alias",
            "cwd": "/tmp/proj",
            "workspaceRoot": "/tmp/proj",
            "toolName": "run_terminal_command",
            "toolInput": {"command": "ls"},
            "toolUseId": "call_cursor_pre",
        }
        canon_payload = {
            **alias_payload,
            "hookEventName": "PreToolUse",
        }
        rec_alias = run_adapter(bridge, alias_payload)
        rec_canon = run_adapter(bridge, canon_payload)
        # Both should match pure format_hook PreToolUse
        want_pre = format_hook(alias_payload, "PreToolUse", {"prefix": "🔧"})
        eq("cursor alias preToolUse recorded", want_pre, rec_alias)
        eq("cursor alias matches PreToolUse", rec_canon, rec_alias)
        if "using" in rec_alias and "run_terminal_command" in rec_alias:
            ok("cursor alias PreToolUse detail")
        else:
            fail("cursor alias PreToolUse detail", repr(rec_alias))

        # beforeShellExecution also maps to PreToolUse
        shell_payload = {
            "hookEventName": "beforeShellExecution",
            "toolName": "run_terminal_command",
            "toolInput": {"command": "ls"},
            "cwd": "/tmp/proj",
        }
        rec_shell = run_adapter(bridge, shell_payload)
        want_shell = format_hook(shell_payload, "PreToolUse", {"prefix": "🔧"})
        eq("cursor alias beforeShellExecution", want_shell, rec_shell)

        (bridge / "relay.toml").unlink(missing_ok=True)

        # --- hook-notify smart dispatch: Grok not Claude unknown ---
        stop_payload = load_fixture("Stop.json")
        rec_smart = run_adapter(bridge, stop_payload, entry="hook-notify.sh")
        want_stop = expected_summary(stop_payload, "Stop")
        eq("hook-notify Grok Stop summary", want_stop, rec_smart)
        if "Claude Code" in rec_smart:
            fail("hook-notify not Claude path", rec_smart)
        else:
            ok("hook-notify not Claude path")
        if "Grok" in rec_smart or "🏁" in rec_smart:
            ok("hook-notify Grok-shaped summary")
        else:
            fail("hook-notify Grok-shaped summary", repr(rec_smart))

        # Claude-shaped unknown still goes to Claude adapter (control)
        claude_unknown = {"hook_event_name": "WeirdFutureEvent"}
        rec_claude = run_adapter(bridge, claude_unknown, entry="hook-notify.sh")
        if "Claude Code" in rec_claude and "WeirdFutureEvent" in rec_claude:
            ok("hook-notify Claude unknown control")
        else:
            fail("hook-notify Claude unknown control", repr(rec_claude))

        # Grok payload must not look like Claude unknown
        for fname in ("Notification.json", "StopFailure.json", "SubagentStop.json"):
            payload = load_fixture(fname)
            rec = run_adapter(bridge, payload, entry="hook-notify.sh")
            if rec and "Claude Code" not in rec:
                ok(f"hook-notify Grok {Path(fname).stem}")
            else:
                fail(f"hook-notify Grok {Path(fname).stem}", repr(rec))

        # hook-notify-grok.sh shim also delivers Stop
        rec_shim = run_adapter(bridge, stop_payload, entry="hook-notify-grok.sh")
        eq("hook-notify-grok Stop summary", want_stop, rec_shim)

    finally:
        shutil.rmtree(bridge, ignore_errors=True)

    print()
    print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
    if FAILURES:
        print("Failed:", ", ".join(FAILURES))
    return 0 if FAIL == 0 else 1


def test_grok_adapter_e2e() -> None:
    """pytest entry — dual-runs the standalone script checks."""
    rc = main()
    assert rc == 0, f"script-style checks failed (exit {rc}); see PASS/FAIL lines above"


if __name__ == "__main__":
    raise SystemExit(main())

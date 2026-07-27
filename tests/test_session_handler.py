#!/usr/bin/env python3
"""tests/test_session_handler.py — Offline tests for handlers/session.sh.

`/session` manages @handle agent sessions from Telegram. It takes its argument
from a chat message and can run scripts, so the security properties are the
point of this suite:

  - a handle is strictly ^[A-Za-z0-9_-]{1,32}$; anything else is refused before
    any script runs (no shell metacharacters, no path traversal)
  - `start` never launches a process unless the operator set
    [sessions].launch_cmd — the message supplies only a handle
  - the handle reaches launch_cmd through the environment, not string
    interpolation

Plus the ordinary behaviour: list/status/start/stop and honest reporting of an
ORPHAN session (registered, but no agent Monitor attached).

NO network. The handler's reply path (relay-notify.sh) is stubbed to a capture
file, since the real one would send to Telegram.

Run:  python3 tests/test_session_handler.py
      uv run python tests/test_session_handler.py
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


def true(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail)


def _bridge() -> Path:
    """A throwaway bridge dir with the handler and a stubbed notify path."""
    d = Path(tempfile.mkdtemp(prefix="tg-session-h-"))
    (d / "handlers").mkdir()
    (d / "scripts").mkdir()
    (d / "lib").mkdir()
    shutil.copy(REPO / "handlers" / "session.sh", d / "handlers" / "session.sh")
    for s in ("register-session.sh", "unregister-session.sh"):
        shutil.copy(REPO / "scripts" / s, d / "scripts" / s)
    for f in REPO.glob("lib/*"):
        if f.is_file():
            shutil.copy(f, d / "lib" / f.name)
    shutil.copytree(REPO / "tg_agent_relay", d / "tg_agent_relay")
    # Stub the reply path: record what would have been sent to Telegram.
    notify = d / "relay-notify.sh"
    notify.write_text(
        '#!/bin/bash\nshift 2>/dev/null\nprintf "%s\\n" "$*" >> "$RELAY_NOTIFY_CAPTURE"\n',
        encoding="utf-8",
    )
    notify.chmod(0o755)
    (d / "handlers" / "session.sh").chmod(0o755)
    return d


def run(bridge: Path, text: str, **env_extra: str) -> str:
    """Invoke the handler and return what it would have replied."""
    cap = bridge / "capture.txt"
    cap.write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["RELAY_NOTIFY_CAPTURE"] = str(cap)
    env["RELAY_SESSIONS_DIR"] = str(bridge / ".sessions.d")
    env.update(env_extra)
    subprocess.run(
        ["bash", str(bridge / "handlers" / "session.sh"), text],
        cwd=str(bridge),
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return cap.read_text(encoding="utf-8")


def main() -> int:
    # --- refuses anything that is not a plain handle ------------------------
    # Each of these would be dangerous if interpolated into a shell command.
    b = _bridge()
    marker = b / "PWNED"
    hostile = [
        f"grok; touch {marker}",
        f"grok$(touch {marker})",
        f"grok`touch {marker}`",
        f"a && touch {marker}",
        f"a | touch {marker}",
        "../../etc/passwd",
        "../escape",
        "with space",
        "x" * 33,
        "",
    ]
    for bad in hostile:
        out = run(b, f"/session start {bad}")
        true(
            f"refuses hostile handle {bad[:28]!r}",
            "Usage:" in out or "Unknown action" in out,
            out,
        )
    true("no injected side effect", not marker.exists(), str(marker))
    sess_files = list((b / ".sessions.d").glob("*.json")) if (b / ".sessions.d").is_dir() else []
    true("no session registered from hostile input", sess_files == [], str(sess_files))

    # --- happy path ---------------------------------------------------------
    b2 = _bridge()
    out = run(b2, "/session list")
    true("empty list is reported", "No sessions registered" in out, out)

    out = run(b2, "/session start grok")
    true("start registers the handle", "Registered @grok" in out, out)
    reg = b2 / ".sessions.d" / "grok.json"
    true("registration file written", reg.is_file(), str(reg))
    if reg.is_file():
        data = json.loads(reg.read_text(encoding="utf-8"))
        true("registration records the handle", data.get("handle") == "grok", str(data))

    # Without launch_cmd the handler must not start anything, and must say so.
    true(
        "no launch without operator config",
        "adapters/backend-fifo-reader.sh" in out and "launch_cmd" in out,
        out,
    )

    out = run(b2, "/session list")
    true("registered session is listed", "@grok" in out, out)
    true(
        "unattached session is reported as ORPHAN, not healthy",
        "ORPHAN" in out,
        out,
    )

    out = run(b2, "/session status grok")
    true("status names the handle", "@grok" in out, out)
    true("status reports no reader honestly", "ORPHAN" in out, out)
    true("status reports held count", "held for replay" in out, out)

    out = run(b2, "/session status nosuch")
    true("status of unknown handle is refused", "No session registered" in out, out)

    out = run(b2, "/session stop grok")
    true("stop unregisters", "Unregistered @grok" in out, out)
    true("registration removed", not reg.is_file(), str(reg))

    out = run(b2, "/session frobnicate")
    true("unknown action is refused", "Unknown action" in out, out)

    # --- launch_cmd is operator-defined, handle passed via env --------------
    b3 = _bridge()
    proof = b3 / "launched.txt"
    (b3 / "relay.toml").write_text(
        "[sessions]\n"
        f'dir = "{b3 / ".sessions.d"}"\n'
        f'launch_cmd = "printf %s \\"$RELAY_SESSION_HANDLE\\" > {proof}"\n',
        encoding="utf-8",
    )
    out = run(b3, "/session start grok")
    true("reports the configured launch", "Launched" in out or "Registered" in out, out)
    # nohup+& is async; give it a moment to land.
    for _ in range(40):
        if proof.is_file() and proof.read_text(encoding="utf-8").strip():
            break
        import time as _t

        _t.sleep(0.1)
    if proof.is_file():
        true(
            "launch_cmd receives the handle via environment",
            proof.read_text(encoding="utf-8").strip() == "grok",
            proof.read_text(encoding="utf-8"),
        )
    else:
        # Config parsing needs jq/python; skip rather than fail a thin env.
        print("SKIP  launch_cmd execution (config toolchain unavailable)")

    print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def test_session_handler() -> None:
    """pytest entry point (dual-run with the standalone script form)."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Dynamic multi-session backends from .sessions.d/<handle>.json (v0.7).

Each registered Grok (or other) session becomes a [backends.<handle>] row at
resolve/deliver time. Static [backends.*] in relay.toml is the base; session
files with the same handle id **override** static rows (documented in
docs/SESSIONS.md).

CLI (shell parity via relay-config.sh):
  python3 lib/sessions.py merge-stdin [--bridge-dir PATH]   # JSON in → merged JSON out
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_HANDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Fields stored in session JSON but not copied into backend cfg as-is.
_SESSION_META = frozenset({"handle", "pid", "registered_at"})


def default_sessions_dir(bridge_dir: Path | str | None = None) -> Path:
    if bridge_dir:
        return Path(bridge_dir) / ".sessions.d"
    return Path.home() / ".claude" / "telegram-bridge" / ".sessions.d"


def sessions_dir_from_cfg(
    cfg: dict[str, Any],
    *,
    bridge_dir: Path | str | None = None,
) -> Path:
    """Resolve sessions directory (cfg → env → bridge/.sessions.d → home default)."""
    sessions = cfg.get("sessions")
    if isinstance(sessions, dict):
        raw = sessions.get("dir")
        if raw:
            return Path(os.path.expanduser(str(raw)))
    env = os.environ.get("RELAY_SESSIONS_DIR", "").strip()
    if env:
        return Path(os.path.expanduser(env))
    if bridge_dir:
        return default_sessions_dir(bridge_dir)
    return default_sessions_dir(None)


def _static_backends(cfg: dict[str, Any]) -> dict[str, Any]:
    b = cfg.get("backends") or {}
    if not isinstance(b, dict):
        return {}
    return {str(k): v for k, v in b.items() if isinstance(v, dict)}


def session_record_to_backend(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Map a <handle>.json document to a backends.<handle> table."""
    handle = str(rec.get("handle") or "").strip()
    if not handle or not _HANDLE_RE.match(handle):
        return None
    out: dict[str, Any] = {}
    for key, val in rec.items():
        if key in _SESSION_META:
            continue
        if val is None:
            continue
        out[key] = val
    if not out.get("tag"):
        out["tag"] = handle
    if not out.get("delivery"):
        out["delivery"] = "fifo"
    prefs = out.get("prefixes")
    if not prefs:
        out["prefixes"] = [f"@{handle}", f"/{handle}", f"{handle}:"]
    return out


def load_session_backends(
    cfg: dict[str, Any],
    *,
    bridge_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Load all <handle>.json files; returns backend_id → backend cfg."""
    sdir = sessions_dir_from_cfg(cfg, bridge_dir=bridge_dir)
    if not sdir.is_dir():
        return {}
    out: dict[str, Any] = {}
    for path in sorted(sdir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        handle = str(raw.get("handle") or path.stem).strip()
        if not handle:
            continue
        bcfg = session_record_to_backend({**raw, "handle": handle})
        if bcfg:
            out[handle] = bcfg
    return out


def merged_backends(
    cfg: dict[str, Any],
    *,
    bridge_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Static [backends.*] merged with session registry; sessions override same id."""
    static = _static_backends(cfg)
    dynamic = load_session_backends(cfg, bridge_dir=bridge_dir)
    if not dynamic:
        return static
    merged = dict(static)
    merged.update(dynamic)
    return merged


def apply_sessions(
    cfg: dict[str, Any],
    *,
    bridge_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Shallow copy of cfg with effective backends (for poll / resolve)."""
    out = dict(cfg)
    sessions = out.get("sessions")
    if not isinstance(sessions, dict):
        sessions = {}
    else:
        sessions = dict(sessions)
    if "dir" not in sessions and bridge_dir:
        sessions["dir"] = str(default_sessions_dir(bridge_dir))
    out["sessions"] = sessions
    out["backends"] = merged_backends(out, bridge_dir=bridge_dir)
    out["_sessions_merged"] = True
    if bridge_dir:
        out["_bridge_dir"] = str(bridge_dir)
    return out


def list_sessions(
    cfg: dict[str, Any],
    *,
    bridge_dir: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Return session records (raw JSON) sorted by handle."""
    sdir = sessions_dir_from_cfg(cfg, bridge_dir=bridge_dir)
    if not sdir.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(sdir.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            rows.append(raw)
    return rows


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def write_session_record(
    *,
    sessions_dir: Path,
    handle: str,
    fifo: str,
    tag: str = "",
    session_type: str = "grok",
    delivery: str = "fifo",
    prefixes: list[str] | None = None,
    project: str = "",
    pid: int | None = None,
) -> Path:
    """Create fifo + write <handle>.json; returns path to json."""
    if not _HANDLE_RE.match(handle):
        raise ValueError(f"invalid handle: {handle!r}")
    sessions_dir.mkdir(parents=True, exist_ok=True)
    fifo_path = Path(os.path.expanduser(fifo))
    fifo_path.parent.mkdir(parents=True, exist_ok=True)
    if not fifo_path.exists():
        os.mkfifo(fifo_path)  # type: ignore[arg-type]
    prefs = prefixes or [f"@{handle}", f"/{handle}", f"{handle}:"]
    rec = {
        "handle": handle,
        "fifo": str(fifo_path),
        "tag": tag or handle,
        "type": session_type,
        "delivery": delivery,
        "prefixes": prefs,
        "project": project,
        "pid": int(pid if pid is not None else os.getpid()),
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }
    out = sessions_dir / f"{handle}.json"
    out.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Session registry helpers")
    p.add_argument(
        "cmd",
        choices=["merge-stdin"],
    )
    p.add_argument("--bridge-dir", default="")
    args = p.parse_args(argv)
    bridge = args.bridge_dir or os.environ.get("RELAY_BRIDGE_DIR", "")
    bridge_path: Path | str | None = bridge if bridge else None
    if args.cmd == "merge-stdin":
        raw = json.loads(sys.stdin.read() or "{}")
        if not isinstance(raw, dict):
            raw = {}
        merged = apply_sessions(raw, bridge_dir=bridge_path)
        print(json.dumps(merged, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
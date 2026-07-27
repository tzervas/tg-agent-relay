"""Forum topic titles, binding resolve, and createForumTopic (mockable urllib).

Operator model: flat forum topics encode session / repo / workstream in titles;
[[chats]] overlay rows carry session, workstream, platform, handle metadata.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tg_agent_relay.config import cfg_get, load_config

UrlopenFn = Callable[[urllib.request.Request, float], bytes]


@dataclass(frozen=True)
class ResolvedTarget:
    chat_id: str
    thread_id: str
    title: str = ""
    match_kind: str = ""


def session_short(session: str) -> str:
    """Short label for topic titles (first 4 chars when long)."""
    s = (session or "").strip()
    if len(s) <= 8:
        return s
    return s[:4]


def repo_short(project: str) -> str:
    if not project:
        return ""
    p = project.strip()
    if "/" in p or p.startswith("~"):
        return Path(p).expanduser().name
    return p


def build_topic_title(session: str, project: str = "", workstream: str = "") -> str:
    """Build flat forum topic title per P13 encoding."""
    ss = session_short(session)
    if not ss:
        return ""
    ws = (workstream or "").strip()
    proj = repo_short(project)
    if ws and proj:
        return f"{ss} · {proj} · {ws}"
    if proj:
        return f"{ss} · {proj}"
    return ss


def parse_topic_title(title: str) -> dict[str, str]:
    """Best-effort parse of title pattern → session_short, project, workstream."""
    t = (title or "").strip()
    if not t:
        return {}
    parts = [p.strip() for p in t.split("·")]
    if len(parts) == 1:
        return {"session_short": parts[0]}
    if len(parts) == 2:
        return {"session_short": parts[0], "project": parts[1]}
    return {"session_short": parts[0], "project": parts[1], "workstream": parts[2]}


def _chats(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    raw = cfg.get("chats") or []
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, dict)]


def _row_get(row: dict[str, Any], key: str) -> str:
    v = row.get(key)
    if v is None:
        return ""
    return str(v).strip()


def _match_row(row: dict[str, Any], must: dict[str, str], empty: set[str] | None = None) -> bool:
    for key, val in must.items():
        if not val:
            continue
        if _row_get(row, key) != val:
            return False
    return all(not _row_get(row, key) for key in empty or set())


def find_binding(
    cfg: dict[str, Any],
    *,
    session: str = "",
    project: str = "",
    workstream: str = "",
    platform: str = "",
    handle: str = "",
) -> dict[str, Any] | None:
    """Find best [[chats]] row using outbound resolution tiers."""
    session = (session or "").strip()
    project = (project or "").strip()
    workstream = (workstream or "").strip()
    platform = (platform or "").strip()
    handle = (handle or "").strip()

    tiers: list[tuple[dict[str, str], set[str]]] = []
    if session and project and workstream:
        full = {
            "session": session,
            "project": project,
            "workstream": workstream,
            "platform": platform,
            "handle": handle,
        }
        tiers.append((full, set()))
        tiers.append(
            (
                {"session": session, "project": project, "workstream": workstream},
                {"platform", "handle"},
            )
        )
    if session and project:
        tiers.append(({"session": session, "project": project}, {"workstream"}))
    if session:
        tiers.append(({"session": session}, {"project", "workstream"}))

    chats = _chats(cfg)
    for must, empty in tiers:
        crit = {k: v for k, v in must.items() if v}
        if not crit:
            continue
        for row in chats:
            if _match_row(row, crit, empty):
                return row
    return None


def platform_chat_id(cfg: dict[str, Any], platform: str) -> str:
    platform = (platform or "").strip().lower()
    if not platform:
        return ""
    pcs = cfg_get(cfg, "threads.platform_chats", {}) or {}
    if not isinstance(pcs, dict):
        return ""
    for key, val in pcs.items():
        if str(key).strip().lower() == platform and val is not None:
            return str(val)
    return ""


def allowed_create_chat_ids(cfg: dict[str, Any], allowed_chat_id: str = "") -> set[str]:
    out: set[str] = set()
    pcs = cfg_get(cfg, "threads.platform_chats", {}) or {}
    if isinstance(pcs, dict):
        for val in pcs.values():
            if val is not None and str(val).strip():
                out.add(str(val).strip())
    extra = cfg_get(cfg, "threads.allowed_chat_ids", []) or []
    if isinstance(extra, list):
        for val in extra:
            if val is not None and str(val).strip():
                out.add(str(val).strip())
    if allowed_chat_id and str(allowed_chat_id).strip():
        out.add(str(allowed_chat_id).strip())
    return out


def chat_allowed_for_topic_create(
    cfg: dict[str, Any], chat_id: str, *, allowed_chat_id: str = ""
) -> bool:
    if not cfg_get(cfg, "threads.enabled", False):
        return False
    cid = str(chat_id or "").strip()
    if not cid:
        return False
    return cid in allowed_create_chat_ids(cfg, allowed_chat_id)


def _pair_from_row(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("chat_id", "")), str(row.get("thread_id") or "")


def resolve_outbound(
    cfg: dict[str, Any],
    *,
    chat_id: str = "",
    thread_id: str = "",
    session: str = "",
    project: str = "",
    workstream: str = "",
    platform: str = "",
    handle: str = "",
    backend: str = "",
    allowed_chat_id: str = "",
) -> ResolvedTarget:
    """Outbound resolution order (P13 design)."""
    chat_id = (chat_id or "").strip()
    thread_id = (thread_id or "").strip()
    project = (project or "").strip()
    session = (session or "").strip()
    workstream = (workstream or "").strip()
    platform = (platform or "").strip()
    handle = (handle or "").strip()

    if chat_id and thread_id:
        title = build_topic_title(session, project, workstream) if session else ""
        return ResolvedTarget(chat_id, thread_id, title=title, match_kind="explicit")

    row = find_binding(
        cfg,
        session=session,
        project=project,
        workstream=workstream,
        platform=platform,
        handle=handle,
    )
    if row:
        cid, tid = _pair_from_row(row)
        title = build_topic_title(
            _row_get(row, "session") or session,
            _row_get(row, "project") or project,
            _row_get(row, "workstream") or workstream,
        )
        return ResolvedTarget(cid, tid, title=title, match_kind="binding")

    if project:
        from tg_agent_relay.routing import lookup_chat, lookup_project

        cid, tid = lookup_project(cfg, project)
        if cid:
            return ResolvedTarget(cid, tid, match_kind="project")
        if backend:
            cid, tid = lookup_chat(cfg, backend, project)
            if cid:
                return ResolvedTarget(cid, tid, match_kind="backend_project")

    if platform:
        pc = platform_chat_id(cfg, platform)
        if pc:
            return ResolvedTarget(pc, "", match_kind="platform")

    hints = any((session, project, workstream, platform, handle, backend))
    if allowed_chat_id and hints:
        return ResolvedTarget(str(allowed_chat_id), "", match_kind="fallback")

    return ResolvedTarget("", "", match_kind="none")


def overlay_key(chat_id: str, thread_id: str) -> str:
    return f"{chat_id}|{thread_id or ''}"


def upsert_overlay_binding(overlay_path: Path, row: dict[str, Any]) -> dict[str, Any]:
    """Upsert one chats[] row in .chats.d/bindings.json (same key as project bind)."""
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    doc: dict[str, Any] = {"chats": []}
    if overlay_path.is_file():
        try:
            raw = json.loads(overlay_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                doc = raw
        except (OSError, json.JSONDecodeError) as _exc:
            raise ValueError(f"corrupt overlay: {overlay_path}") from _exc
    chats = doc.get("chats")
    if not isinstance(chats, list):
        chats = []
    cid = str(row.get("chat_id", ""))
    tid = str(row.get("thread_id") or "")
    key = overlay_key(cid, tid)
    kept = [
        c
        for c in chats
        if isinstance(c, dict)
        and overlay_key(str(c.get("chat_id", "")), str(c.get("thread_id") or "")) != key
    ]
    kept.append(row)
    doc["chats"] = kept
    tmp = overlay_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    tmp.replace(overlay_path)
    return doc


def binding_row(
    *,
    chat_id: str | int,
    thread_id: str | int | None,
    backend: str = "",
    project: str = "",
    session: str = "",
    workstream: str = "",
    platform: str = "",
    handle: str = "",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "chat_id": int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    }
    if thread_id is not None and str(thread_id).strip() not in ("", "0"):
        row["thread_id"] = int(thread_id) if str(thread_id).isdigit() else thread_id
    else:
        row["thread_id"] = None
    if backend:
        row["backend"] = backend
    if project:
        row["project"] = project
    if session:
        row["session"] = session
    if workstream:
        row["workstream"] = workstream
    if platform:
        row["platform"] = platform
    if handle:
        row["handle"] = handle
    return row


class CreateRateLimiter:
    """Simple hourly cap for createForumTopic (persisted JSON for operators)."""

    def __init__(self, max_per_hour: int, state_path: Path | None = None) -> None:
        self.max_per_hour = max(1, int(max_per_hour))
        self.state_path = state_path

    def _load(self) -> list[float]:
        if not self.state_path or not self.state_path.is_file():
            return []
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return [float(x) for x in raw]
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as _exc:
            return []
        return []

    def _save(self, times: list[float]) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(times), encoding="utf-8")

    def allow(self, now: float | None = None) -> bool:
        t = now if now is not None else time.time()
        window_start = t - 3600.0
        times = [x for x in self._load() if x >= window_start]
        return len(times) < self.max_per_hour

    def record(self, now: float | None = None) -> None:
        t = now if now is not None else time.time()
        window_start = t - 3600.0
        times = [x for x in self._load() if x >= window_start]
        times.append(t)
        self._save(times)


def _api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def create_forum_topic(
    token: str,
    chat_id: str | int,
    name: str,
    *,
    urlopen: UrlopenFn | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Call Telegram createForumTopic; returns API result dict (raises on HTTP/ok=false)."""
    if not token or not str(chat_id).strip() or not (name or "").strip():
        raise ValueError("token, chat_id, and name are required")
    data = urllib.parse.urlencode({"chat_id": str(chat_id), "name": name[:128]}).encode()
    req = urllib.request.Request(
        _api_url(token, "createForumTopic"),
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    opener = urlopen or _default_urlopen
    raw = opener(req, timeout)
    payload = json.loads(raw.decode("utf-8"))
    if not payload.get("ok"):
        desc = payload.get("description") or "createForumTopic failed"
        raise RuntimeError(str(desc))
    return payload.get("result") or {}


def _default_urlopen(req: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def ensure_topic(
    cfg: dict[str, Any],
    *,
    token: str,
    chat_id: str,
    session: str,
    project: str = "",
    workstream: str = "",
    backend: str = "",
    platform: str = "",
    handle: str = "",
    overlay_path: Path,
    allowed_chat_id: str = "",
    urlopen: UrlopenFn | None = None,
) -> tuple[str, str, str]:
    """Return (chat_id, thread_id, title); create topic + bind when missing."""
    if not chat_allowed_for_topic_create(cfg, chat_id, allowed_chat_id=allowed_chat_id):
        raise PermissionError(f"chat_id not allowed for topic create: {chat_id}")
    title = build_topic_title(session, project, workstream)
    if not title:
        raise ValueError("session required to build topic title")

    existing = find_binding(
        cfg,
        session=session,
        project=project,
        workstream=workstream,
        platform=platform,
        handle=handle,
    )
    if existing:
        cid, tid = _pair_from_row(existing)
        if cid and tid:
            return cid, tid, title

    max_h = int(cfg_get(cfg, "threads.max_creates_per_hour", 30) or 30)
    state = overlay_path.parent / ".threads.d" / "create_times.json"
    limiter = CreateRateLimiter(max_h, state)
    if not limiter.allow():
        raise RuntimeError("topic create rate limit exceeded")

    result = create_forum_topic(token, chat_id, title, urlopen=urlopen)
    thread_id = str(result.get("message_thread_id") or result.get("id") or "")
    if not thread_id:
        raise RuntimeError("createForumTopic returned no message_thread_id")
    limiter.record()

    row = binding_row(
        chat_id=chat_id,
        thread_id=thread_id,
        backend=backend,
        project=project,
        session=session,
        workstream=workstream,
        platform=platform,
        handle=handle,
    )
    upsert_overlay_binding(overlay_path, row)
    return str(chat_id), thread_id, title


def resolve_from_environ(
    bridge_dir: Path | str | None = None,
    *,
    env: dict[str, str] | None = None,
) -> ResolvedTarget:
    """Resolve using process env (RELAY_* and ALLOWED_CHAT_ID)."""
    e = env if env is not None else os.environ
    root = Path(bridge_dir) if bridge_dir else Path(__file__).resolve().parents[1]
    cfg = load_config(root / "relay.toml", bridge_dir=root)
    allowed = e.get("ALLOWED_CHAT_ID") or ""
    return resolve_outbound(
        cfg,
        chat_id=e.get("RELAY_CHAT_ID") or "",
        thread_id=e.get("RELAY_THREAD_ID") or "",
        session=e.get("RELAY_SESSION") or "",
        project=e.get("RELAY_PROJECT") or e.get("RELAY_REPO") or "",
        workstream=e.get("RELAY_WORKSTREAM") or "",
        platform=e.get("RELAY_PLATFORM") or "",
        handle=e.get("RELAY_AGENT_HANDLE") or "",
        backend=e.get("RELAY_BACKEND") or "",
        allowed_chat_id=allowed,
    )


def threads_enabled(cfg: dict[str, Any]) -> bool:
    v = cfg_get(cfg, "threads.enabled", False)
    return v in (True, "true", "1", 1, "yes", "on")


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Forum thread helpers")
    sub = p.add_subparsers(dest="cmd", required=True)

    ro = sub.add_parser("resolve-outbound", help="Print chat_id|thread_id|title|kind from env")
    ro.add_argument("--bridge-dir", default="")

    tp = sub.add_parser("build-title")
    tp.add_argument("--session", required=True)
    tp.add_argument("--project", default="")
    tp.add_argument("--workstream", default="")

    args = p.parse_args(argv)
    if args.cmd == "build-title":
        print(build_topic_title(args.session, args.project, args.workstream))
        return 0
    if args.cmd == "resolve-outbound":
        root = Path(args.bridge_dir) if args.bridge_dir else Path(__file__).resolve().parents[1]
        target = resolve_from_environ(root)
        print(f"{target.chat_id}|{target.thread_id}|{target.title}|{target.match_kind}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

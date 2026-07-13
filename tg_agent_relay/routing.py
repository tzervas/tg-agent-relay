"""Routing facade — re-exports lib/routing.py + RouteResult protocol type."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from tg_agent_relay.protocols import RouteResult

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import routing as _routing  # type: ignore  # noqa: E402


def resolve(cfg: dict[str, Any], chat_id: str, thread_id: str, text: str) -> RouteResult:
    b, p, t, k = _routing.resolve(cfg, chat_id, thread_id, text)
    return RouteResult(backend=b, project=p, text=t, match_kind=k)


def project_from_cwd(cfg: dict[str, Any], cwd: str) -> str:
    return _routing.project_from_cwd(cfg, cwd)


def strip_prefix(cfg: dict[str, Any], text: str) -> tuple[str, str, str] | None:
    return _routing.strip_prefix(cfg, text)


def chat_binding(cfg: dict[str, Any], chat_id: str, thread_id: str = "") -> dict | None:
    return _routing.chat_binding(cfg, chat_id, thread_id)


def lookup_chat(cfg: dict[str, Any], backend: str, project: str = "") -> tuple[str, str]:
    """Return (chat_id, thread_id) using same preference as shell route_lookup_chat."""
    chats = cfg.get("chats") or []
    if not isinstance(chats, list):
        return "", ""

    def match(pred):
        for c in chats:
            if isinstance(c, dict) and pred(c):
                return str(c.get("chat_id", "")), str(c.get("thread_id") or "")
        return None

    if project and backend:
        hit = match(
            lambda c: (
                str(c.get("project") or "") == project and str(c.get("backend") or "") == backend
            )
        )
        if hit:
            return hit
    if project:
        hit = match(lambda c: str(c.get("project") or "") == project and not c.get("backend"))
        if hit:
            return hit
        hit = match(lambda c: str(c.get("project") or "") == project)
        if hit:
            return hit
    if backend:
        hit = match(lambda c: str(c.get("backend") or "") == backend)
        if hit:
            return hit
    return "", ""

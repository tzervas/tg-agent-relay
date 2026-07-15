#!/usr/bin/env python3
"""lib/routing.py - Python port of lib/routing.sh route resolution.

Pure functions over a config dict (same shape as relay.toml → JSON).
Shell lib/routing.sh remains the runtime default until tg-poll is ported;
this module is unit-testable and the migration target for exclusive use later.

CLI:
  python3 lib/routing.py resolve --config cfg.json --chat-id -1001 --text '@grok hi'
  python3 lib/routing.py project-from-cwd --config cfg.json --cwd /path
  python3 lib/routing.py lookup-chat --config cfg.json --backend claude --project mycelium
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def _backends(cfg: dict[str, Any]) -> dict[str, Any]:
    b = cfg.get("backends") or {}
    return b if isinstance(b, dict) else {}


def _chats(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    c = cfg.get("chats") or []
    if not isinstance(c, list):
        return []
    return [x for x in c if isinstance(x, dict)]


def _projects(cfg: dict[str, Any]) -> dict[str, Any]:
    p = cfg.get("projects") or {}
    return p if isinstance(p, dict) else {}


def _routing(cfg: dict[str, Any]) -> dict[str, Any]:
    r = cfg.get("routing") or {}
    return r if isinstance(r, dict) else {}


def _backend_cfg_get(cfg: dict[str, Any], backend_id: str, field: str, default: str = "") -> str:
    bcfg = _backends(cfg).get(backend_id)
    if not isinstance(bcfg, dict):
        return default
    val = bcfg.get(field)
    if val is None:
        return default
    return str(val)


def has_routing_config(cfg: dict[str, Any]) -> bool:
    """True if backends table or non-empty chats list is configured."""
    if _backends(cfg):
        return True
    return bool(_chats(cfg))


def chat_binding(cfg: dict[str, Any], chat_id: str, thread_id: str = "") -> dict[str, Any] | None:
    """Return first [[chats]] row matching chat_id (+ thread preference).

    Prefer exact thread match; fall back to chat-only rows with no/empty/0 thread_id.
    """
    chats = _chats(cfg)
    cid = str(chat_id)
    tid = str(thread_id or "")
    if tid:
        for c in chats:
            if str(c.get("chat_id", "")) == cid and str(c.get("thread_id", "")) == tid:
                return c
    for c in chats:
        th = c.get("thread_id")
        if str(c.get("chat_id", "")) == cid and (th is None or th == "" or str(th) == "0"):
            return c
    return None


def strip_prefix(cfg: dict[str, Any], text: str) -> tuple[str, str, str] | None:
    """Try every backend's prefixes (longest first).

    Returns (backend_id, project, stripped_text) or None.
    Match when text == pref, text starts with "pref ", or text starts with "pref:".
    """
    best: tuple[int, str, str, str] | None = None  # len, id, project, stripped
    for bid, bcfg in _backends(cfg).items():
        if not isinstance(bcfg, dict):
            continue
        prefs = bcfg.get("prefixes") or []
        if isinstance(prefs, str):
            prefs = [prefs] if prefs else []
        elif not isinstance(prefs, list):
            continue
        project = str(bcfg.get("project") or "")
        for pref in prefs:
            pref = str(pref).strip()
            if not pref:
                continue
            if text == pref or text.startswith(pref + " ") or text.startswith(pref + ":"):
                if best is None or len(pref) > best[0]:
                    if text == pref:
                        stripped = ""
                    elif text.startswith(pref + ":"):
                        stripped = text[len(pref) + 1 :].lstrip()
                    else:
                        stripped = text[len(pref) :].lstrip()
                    best = (len(pref), bid, project, stripped)
    if best is None:
        return None
    return best[1], best[2], best[3]


def resolve(
    cfg: dict[str, Any], chat_id: str, thread_id: str, text: str
) -> tuple[str, str, str, str]:
    """Return (backend, project, stripped_text, match_kind).

    match_kind ∈ chat | prefix | default | none | legacy
    Pipe format: backend|project|text|match_kind
    """
    if not has_routing_config(cfg):
        return "", "", text, "legacy"

    binding = chat_binding(cfg, chat_id, thread_id)
    if binding is not None:
        backend = str(binding.get("backend") or "")
        project = str(binding.get("project") or "")
        # Project-only room (backend empty): sticky project; backend from
        # @prefix inside the room, else project/global default.
        if not backend and project:
            hit = strip_prefix(cfg, text)
            if hit:
                return hit[0], project, hit[2], "chat"
            proj_cfg = _projects(cfg).get(project) or {}
            proj_def = ""
            if isinstance(proj_cfg, dict):
                proj_def = str(proj_cfg.get("default_backend") or "")
            if not proj_def:
                proj_def = str(_routing(cfg).get("default_backend") or "")
            return proj_def, project, text, "chat"
        # Fully sticky (backend + project, or backend-only)
        return backend, project, text, "chat"

    hit = strip_prefix(cfg, text)
    if hit:
        backend, project, stripped = hit
        if not project:
            project = _backend_cfg_get(cfg, backend, "project", "")
        return backend, project, stripped, "prefix"

    default = str(_routing(cfg).get("default_backend") or "")
    if default:
        project = _backend_cfg_get(cfg, default, "project", "")
        return default, project, text, "default"

    require = _routing(cfg).get("require_prefix")
    if require in (True, "true", "1"):
        return "", "", text, "none"
    return "", "", text, "legacy"


def lookup_chat(cfg: dict[str, Any], backend: str, project: str = "") -> tuple[str, str]:
    """Reverse lookup: first [[chats]] entry matching backend (+ project).

    Preference (mirrors shell route_lookup_chat):
      exact backend+project → project-only room → any project match → backend-only.
    Returns (chat_id, thread_id); empty strings when no hit.
    """
    chats = _chats(cfg)
    backend = backend or ""
    project = project or ""

    def _pair(c: dict[str, Any]) -> tuple[str, str]:
        return str(c.get("chat_id", "")), str(c.get("thread_id") or "")

    if project and backend:
        for c in chats:
            if str(c.get("project") or "") == project and str(c.get("backend") or "") == backend:
                return _pair(c)
    if project:
        for c in chats:
            if str(c.get("project") or "") == project and not (c.get("backend") or ""):
                return _pair(c)
        for c in chats:
            if str(c.get("project") or "") == project:
                return _pair(c)
    if backend:
        for c in chats:
            if str(c.get("backend") or "") == backend:
                return _pair(c)
    return "", ""


def lookup_project(cfg: dict[str, Any], project: str) -> tuple[str, str]:
    """Return (chat_id, thread_id) for the primary room of a project."""
    if not project:
        return "", ""
    for c in _chats(cfg):
        if str(c.get("project") or "") == project:
            return str(c.get("chat_id", "")), str(c.get("thread_id") or "")
    return "", ""


def _abs_path(path: str) -> str:
    """Expand ~ and make absolute (realpath -m style; missing paths OK)."""
    expanded = os.path.expanduser(path)
    try:
        return str(Path(expanded).resolve())
    except OSError:
        return expanded


def project_from_cwd(cfg: dict[str, Any], cwd: str) -> str:
    """Map a filesystem path to the longest-matching project slug."""
    if not cwd:
        return ""
    cwd_r = _abs_path(cwd)
    best = ""
    best_len = 0
    for slug, pcfg in _projects(cfg).items():
        if not isinstance(pcfg, dict):
            continue
        candidates: list[str] = []
        root = pcfg.get("root")
        if root:
            candidates.append(str(root))
        wts = pcfg.get("worktrees") or {}
        if isinstance(wts, dict):
            candidates.extend(str(v) for v in wts.values() if v)
        for cand in candidates:
            cand_r = _abs_path(cand)
            if cwd_r == cand_r or cwd_r.startswith(cand_r + os.sep):
                if len(cand_r) > best_len:
                    best_len = len(cand_r)
                    best = str(slug)
    return best


def project_worktree(cfg: dict[str, Any], project: str, backend: str = "") -> str:
    """Resolve cwd from projects.<id>.worktrees.<backend> or root or bare path."""
    if not project:
        return ""
    pcfg = _projects(cfg).get(project)
    if isinstance(pcfg, dict):
        wts = pcfg.get("worktrees") or {}
        if backend and isinstance(wts, dict):
            wt = wts.get(backend)
            if wt:
                return os.path.expanduser(str(wt))
        root = pcfg.get("root")
        if root:
            return os.path.expanduser(str(root))
    # Bare path project
    if project.startswith(("/", "./", "~")) or project.startswith("~"):
        return os.path.expanduser(project)
    return project


def format_tag(cfg: dict[str, Any], backend: str, project: str = "") -> str:
    """Outbound prefix like '[claude · mycelium]' (empty when no backend)."""
    if not backend:
        return ""
    tag = _backend_cfg_get(cfg, backend, "tag", backend)
    style = str(_routing(cfg).get("tag_style") or "bracket")
    proj_disp = project
    if proj_disp and ("/" in proj_disp or proj_disp.startswith("~")):
        proj_disp = os.path.basename(os.path.expanduser(proj_disp))
    if style == "none":
        return ""
    if style == "bare":
        if proj_disp:
            return f"{tag} · {proj_disp}"
        return tag
    # bracket (default)
    if proj_disp:
        return f"[{tag} · {proj_disp}]"
    return f"[{tag}]"


def inbound_tag(backend: str = "", project: str = "") -> str:
    """Tag for stdout/fifo event lines."""
    proj_slug = project
    if proj_slug and ("/" in proj_slug or proj_slug.startswith("~")):
        proj_slug = os.path.basename(os.path.expanduser(proj_slug))
    if backend and proj_slug:
        return f"[telegram:backend:{backend}:project:{proj_slug}]"
    if backend:
        return f"[telegram:backend:{backend}]"
    return "[telegram]"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Route resolution (lib/routing.py)")
    p.add_argument(
        "cmd",
        choices=["resolve", "project-from-cwd", "lookup-chat"],
    )
    p.add_argument("--config", required=True, help="JSON config path (relay.toml converted)")
    p.add_argument("--chat-id", default="")
    p.add_argument("--thread-id", default="")
    p.add_argument("--text", default="")
    p.add_argument("--cwd", default="")
    p.add_argument("--backend", default="")
    p.add_argument("--project", default="")
    args = p.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.cmd == "resolve":
        b, proj, text, kind = resolve(cfg, args.chat_id, args.thread_id, args.text)
        print(f"{b}|{proj}|{text}|{kind}")
    elif args.cmd == "project-from-cwd":
        print(project_from_cwd(cfg, args.cwd))
    else:
        chat_id, thread_id = lookup_chat(cfg, args.backend, args.project)
        print(f"{chat_id}|{thread_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""lib/routing.py - Python port of project/backend route resolution (Track D).

Pure functions over a config dict (same shape as relay.toml → JSON).
Shell lib/routing.sh remains the runtime default until tg-poll is ported;
this module is unit-testable and the migration target for exclusive use later.

CLI:
  python3 lib/routing.py resolve --config cfg.json --chat-id -1001 --text '@grok hi'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _backends(cfg: dict) -> dict[str, Any]:
    b = cfg.get("backends") or {}
    return b if isinstance(b, dict) else {}


def _chats(cfg: dict) -> list[dict]:
    c = cfg.get("chats") or []
    return c if isinstance(c, list) else []


def chat_binding(cfg: dict, chat_id: str, thread_id: str = "") -> dict | None:
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


def strip_prefix(cfg: dict, text: str) -> tuple[str, str, str] | None:
    """Return (backend_id, project, stripped_text) or None."""
    best: tuple[int, str, str, str] | None = None  # len, id, project, stripped
    for bid, bcfg in _backends(cfg).items():
        if not isinstance(bcfg, dict):
            continue
        prefs = bcfg.get("prefixes") or []
        if isinstance(prefs, str):
            prefs = [prefs]
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


def resolve(cfg: dict, chat_id: str, thread_id: str, text: str) -> tuple[str, str, str, str]:
    """Return (backend, project, stripped_text, match_kind)."""
    backends = _backends(cfg)
    chats = _chats(cfg)
    if not backends and not chats:
        return "", "", text, "legacy"

    binding = chat_binding(cfg, chat_id, thread_id)
    if binding is not None:
        backend = str(binding.get("backend") or "")
        project = str(binding.get("project") or "")
        if not backend and project:
            hit = strip_prefix(cfg, text)
            if hit:
                return hit[0], project, hit[2], "chat"
            proj_def = ((cfg.get("projects") or {}).get(project) or {}).get("default_backend") or ""
            if not proj_def:
                proj_def = (cfg.get("routing") or {}).get("default_backend") or ""
            return str(proj_def), project, text, "chat"
        return backend, project, text, "chat"

    hit = strip_prefix(cfg, text)
    if hit:
        backend, project, stripped = hit
        if not project:
            project = str((_backends(cfg).get(backend) or {}).get("project") or "")
        return backend, project, stripped, "prefix"

    default = (cfg.get("routing") or {}).get("default_backend") or ""
    if default:
        project = str((_backends(cfg).get(default) or {}).get("project") or "")
        return str(default), project, text, "default"

    if (cfg.get("routing") or {}).get("require_prefix") in (True, "true", "1"):
        return "", "", text, "none"
    return "", "", text, "legacy"


def project_from_cwd(cfg: dict, cwd: str) -> str:
    if not cwd:
        return ""
    cwd = os.path.expanduser(cwd)
    try:
        cwd = str(Path(cwd).resolve())
    except OSError:
        pass
    best = ""
    best_len = 0
    projects = cfg.get("projects") or {}
    if not isinstance(projects, dict):
        return ""
    for slug, pcfg in projects.items():
        if not isinstance(pcfg, dict):
            continue
        candidates: list[str] = []
        root = pcfg.get("root")
        if root:
            candidates.append(os.path.expanduser(str(root)))
        wts = pcfg.get("worktrees") or {}
        if isinstance(wts, dict):
            candidates.extend(os.path.expanduser(str(v)) for v in wts.values())
        for cand in candidates:
            try:
                cand_r = str(Path(cand).resolve())
            except OSError:
                cand_r = cand
            if cwd == cand_r or cwd.startswith(cand_r + os.sep):
                if len(cand_r) > best_len:
                    best_len = len(cand_r)
                    best = str(slug)
    return best


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["resolve", "project-from-cwd"])
    p.add_argument("--config", required=True, help="JSON config path (relay.toml converted)")
    p.add_argument("--chat-id", default="")
    p.add_argument("--thread-id", default="")
    p.add_argument("--text", default="")
    p.add_argument("--cwd", default="")
    args = p.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.cmd == "resolve":
        b, proj, text, kind = resolve(cfg, args.chat_id, args.thread_id, args.text)
        print(f"{b}|{proj}|{text}|{kind}")
    else:
        print(project_from_cwd(cfg, args.cwd))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

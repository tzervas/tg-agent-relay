"""Construct @repo-branch agent handles (pure functions, no I/O)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final

_BRANCH_PREFIX_RE = re.compile(r"^(?:feat|fix|chore|docs)/", re.I)
_RESERVED: Final[frozenset[str]] = frozenset({"cabal", "orchestrator", "main", "fleet"})
_ORCH_PREFIX_RE = re.compile(
    r"^@(?P<alias>cabal|orchestrator|main|fleet)(?:\s+|:|$)",
    re.I,
)
_HANDLE_PREFIX_RE = re.compile(r"^@([A-Za-z0-9][A-Za-z0-9_-]{0,127})(?:\s+|:|$)")


def repo_short(repo: str) -> str:
    """Last path segment of repo name, lowercased, alnum only, max 16 chars."""
    name = (repo or "").strip()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    name = name.lower()
    out = "".join(ch for ch in name if ch.isalnum())
    return out[:16]


def branch_short(branch: str) -> str:
    """Normalize branch for handle suffix: strip common prefixes, slug, max 20."""
    b = (branch or "").strip()
    b = _BRANCH_PREFIX_RE.sub("", b)
    b = b.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", b)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:20]


def build_handle(*, repo: str = "", branch: str = "", repo_name: str = "") -> str:
    """Full Telegram handle including leading @."""
    r = repo_short(repo or repo_name)
    br = branch_short(branch)
    if not r and not br:
        return ""
    if not r:
        return f"@{br}"
    if not br:
        return f"@{r}"
    return f"@{r}-{br}"


def handle_id(handle: str) -> str:
    """Strip leading @ for session filenames / RELAY_BACKEND derivation."""
    h = (handle or "").strip()
    if h.startswith("@"):
        return h[1:]
    return h


def is_reserved_handle(handle: str) -> bool:
    """True for orchestrator aliases (with or without @)."""
    h = handle_id(handle).lower()
    return h in _RESERVED


def parse_leading_handle(text: str) -> tuple[str, str] | None:
    """If text starts with @handle, return (handle_with_at, stripped_text)."""
    raw = text or ""
    m = _HANDLE_PREFIX_RE.match(raw)
    if not m:
        return None
    token = m.group(0)
    hid = m.group(1)
    at = f"@{hid}"
    rest = raw[m.end() :].lstrip() if token.endswith(":") else raw[len(token) :].lstrip()
    return at, rest


def strip_orchestrator_prefix(text: str) -> tuple[str, str] | None:
    """Reserved @cabal / @orchestrator / @main / @fleet → (alias, stripped)."""
    raw = text or ""
    m = _ORCH_PREFIX_RE.match(raw)
    if not m:
        return None
    alias = m.group("alias").lower()
    token = m.group(0)
    rest = raw[m.end() :].lstrip() if token.endswith(":") else raw[len(token) :].lstrip()
    return alias, rest


def orchestrator_backend_id(cfg: dict, alias: str = "") -> str:
    """Resolve configured orchestrator backend id for reserved aliases."""
    routing = cfg.get("routing") if isinstance(cfg.get("routing"), dict) else {}
    ob = str((routing or {}).get("orchestrator_backend") or "").strip()
    if ob:
        return ob
    default = str((routing or {}).get("default_backend") or "").strip()
    if default:
        return default
    a = (alias or "").strip().lower()
    backends = cfg.get("backends") if isinstance(cfg.get("backends"), dict) else {}
    if a and a in backends:
        return a
    return ""


def build_handle_from_env(env: Mapping[str, str] | None = None) -> str:
    """Construct handle from RELAY_AGENT_HANDLE or RELAY_REPO + RELAY_BRANCH."""
    import os

    e = env if env is not None else os.environ
    explicit = (e.get("RELAY_AGENT_HANDLE") or "").strip()
    if explicit:
        return explicit if explicit.startswith("@") else f"@{explicit}"
    repo = (e.get("RELAY_REPO") or "").strip()
    branch = (e.get("RELAY_BRANCH") or "").strip()
    return build_handle(repo=repo, branch=branch)


def backend_id_from_handle(handle: str) -> str:
    """RELAY_BACKEND when multi-session: handle id without @."""
    return handle_id(handle)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os

    p = argparse.ArgumentParser(description="Build agent @handle from repo/branch")
    p.add_argument("--repo", default=os.environ.get("RELAY_REPO", ""))
    p.add_argument("--branch", default=os.environ.get("RELAY_BRANCH", ""))
    p.add_argument("--handle", default="", help="Parse/strip only")
    args = p.parse_args(argv)
    if args.handle:
        hit = parse_leading_handle(args.handle)
        if hit:
            print(hit[0])
            print(hit[1])
        else:
            print("")
        return 0
    print(build_handle(repo=args.repo, branch=args.branch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

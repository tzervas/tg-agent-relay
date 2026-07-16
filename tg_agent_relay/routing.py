"""Routing — project/backend resolve with shell parity (issue #24).

Join API (stable, docs/AGENT_INTERFACES.md):
  resolve(cfg, chat_id, thread_id, text) -> RouteResult
  project_from_cwd(cfg, cwd) -> str
  lookup_chat(cfg, backend, project) -> tuple[str, str]
  RouteResult.as_pipe() -> "backend|project|text|match_kind"

Implementation lives in lib/routing.py (pure functions); this module wraps
results as RouteResult and re-exports helpers for package consumers.

CLI:
  python -m tg_agent_relay.cli route --config cfg.json --chat-id -1 --text '@grok hi'
  python -m tg_agent_relay.routing resolve --config cfg.json --chat-id -1 --text hi
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from tg_agent_relay.protocols import RouteResult

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import routing as _routing  # type: ignore  # noqa: E402

try:
    import sessions as _sessions  # type: ignore  # noqa: E402
except ImportError:
    _sessions = None  # type: ignore[assignment]

# Re-export pure helpers for direct use / tests
chat_binding = _routing.chat_binding
strip_prefix = _routing.strip_prefix
has_routing_config = _routing.has_routing_config
lookup_project = _routing.lookup_project
project_worktree = _routing.project_worktree
format_tag = _routing.format_tag
inbound_tag = _routing.inbound_tag

if _sessions is not None:
    apply_sessions = _sessions.apply_sessions
    load_session_backends = _sessions.load_session_backends
    merged_backends = _sessions.merged_backends
else:  # pragma: no cover
    apply_sessions = None  # type: ignore[assignment]
    load_session_backends = None  # type: ignore[assignment]
    merged_backends = None  # type: ignore[assignment]


def resolve(cfg: dict[str, Any], chat_id: str, thread_id: str, text: str) -> RouteResult:
    """Resolve inbound message to backend/project/text/match_kind."""
    b, p, t, k = _routing.resolve(cfg, chat_id, thread_id, text)
    return RouteResult(backend=b, project=p, text=t, match_kind=k)


def project_from_cwd(cfg: dict[str, Any], cwd: str) -> str:
    """Map filesystem path to longest-matching project slug."""
    return _routing.project_from_cwd(cfg, cwd)


def lookup_chat(cfg: dict[str, Any], backend: str, project: str = "") -> tuple[str, str]:
    """Return (chat_id, thread_id) using shell route_lookup_chat preference order."""
    return _routing.lookup_chat(cfg, backend, project)


def main(argv: list[str] | None = None) -> int:
    """CLI shim — same commands as lib/routing.py main."""
    return _routing.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

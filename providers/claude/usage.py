"""Claude Code usage collector (delegates to usage_ingest recursive scan)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def default_projects_dir() -> str:
    return str(Path.home() / ".claude" / "projects")


def collect_usage(base: Path) -> list[Any]:
    import sys

    lib = Path(__file__).resolve().parents[2] / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    from usage_ingest import _collect_claude_code  # type: ignore

    return _collect_claude_code(base)

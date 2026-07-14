"""OpenAI / ChatGPT / OpenAI-compatible usage collector stub.

OpenAI API usage lives in the cloud dashboard or org exports — not in a
Claude/Grok-style local session JSONL tree this relay scrapes today.

This stub:
* never fabricates token rows
* raises ``NoLocalUsageLogs`` so ``usage_ingest`` records an honest skip

Future: parse Codex CLI logs, Cursor OpenAI sessions, or a user-supplied
export directory under ``base``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SKIP_REASON = (
    "no local usage logs (OpenAI/ChatGPT usage is cloud-side or tool-specific; "
    "no scrapeable transcript tree for the relay yet)"
)


class NoLocalUsageLogs(Exception):
    """Honest skip: no local OpenAI usage transcripts under base."""


def default_usage_dir() -> str:
    """Placeholder root (Codex/Cursor may use subdirs later)."""
    return str(Path.home() / ".openai")


def collect_usage(base: Path) -> list[Any]:
    """Return usage rows for OpenAI-family local logs under ``base``.

    Today always raises :class:`NoLocalUsageLogs`. Reserved for future
    Codex / Cursor / export parsers.
    """
    _ = base
    raise NoLocalUsageLogs(SKIP_REASON)

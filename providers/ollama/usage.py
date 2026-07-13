"""Ollama / llama.cpp usage collector stub (issue #33).

Ollama and llama.cpp do **not** write Claude/Grok-style session transcripts
with per-turn token usage that this relay can scrape. This module registers
an honest empty collector so ``source = "ollama"`` is a known
``usage_ingest`` adapter:

* never fabricates token rows
* when there are no local logs, surfaces a clear skipped reason via
  ``NoLocalUsageLogs`` (collected as ``skipped`` by ``usage_ingest.collect``)

A future implementation may parse Ollama server logs, ``/api/ps`` history,
or llama.cpp metrics under ``base`` and return real ``UsageRow`` values
instead of raising.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Canonical reason string (docs + exception message). Keep stable for greps.
SKIP_REASON = "no local usage logs (ollama/llama.cpp leave no token transcripts for the relay)"


class NoLocalUsageLogs(Exception):
    """Honest skip signal: no scrapeable local usage transcripts.

    ``usage_ingest.collect`` records
    ``\"{source}: collection error: NoLocalUsageLogs\"`` in ``skipped`` when
    this is raised and no rows were produced. That is intentional — not a
    bug — until a real log adapter exists.
    """


def default_usage_dir() -> str:
    """Placeholder path for catalog / future log roots.

    Ollama's model blobs live under ``~/.ollama`` but are **not** usage
    transcripts. Pointing ``[usage].projects_dir`` here is optional and
    does not invent token counts.
    """
    return str(Path.home() / ".ollama")


def collect_usage(base: Path) -> list[Any]:
    """Return usage rows for Ollama/llama.cpp local logs under ``base``.

    Today always raises :class:`NoLocalUsageLogs` so callers get an explicit
    skip reason rather than a silent empty success that implies logs were
    scanned. Never fabricates rows.

    Parameters
    ----------
    base:
        Directory that *would* hold scrapeable usage logs once supported.
        Reserved for future best-effort scanning; ignored while the stub
        has nothing to parse.
    """
    _ = base  # reserved for future log discovery under base
    raise NoLocalUsageLogs(SKIP_REASON)

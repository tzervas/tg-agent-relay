"""Claude Code usage collector — walks session transcripts under projects_dir.

Registered on the Claude Provider as ``usage_collect``. usage_ingest.ADAPTERS
picks this up via the providers registry (issue #31); a local copy remains in
lib/usage_ingest.py only as a fallback when ``providers`` cannot be imported.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def default_projects_dir() -> str:
    return str(Path.home() / ".claude" / "projects")


def _usage_ingest():
    """Lazy import of usage_ingest helpers (UsageRow, infer_provider, parse)."""
    import sys

    lib = Path(__file__).resolve().parents[2] / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    import usage_ingest as ui  # type: ignore

    return ui


def collect_usage(base: Path) -> list[Any]:
    """Recursively walk ``base/**/*.jsonl`` (Claude Code session layout).

    Project slug is the first path component under ``base``. Best-effort per
    file and per line — never raises for missing/malformed data.
    """
    ui = _usage_ingest()
    rows: list[Any] = []
    try:
        transcripts = sorted(base.rglob("*.jsonl"))
    except OSError:
        return rows

    for jsonl_path in transcripts:
        try:
            rel = jsonl_path.relative_to(base)
        except ValueError:
            continue
        project = rel.parts[0] if rel.parts else jsonl_path.parent.name
        try:
            with jsonl_path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict) or obj.get("type") != "assistant":
                        continue
                    message = obj.get("message")
                    if not isinstance(message, dict):
                        continue
                    usage = message.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    ts = ui._parse_iso8601(obj.get("timestamp"))
                    if ts is None:
                        continue
                    model = str(message.get("model") or "unknown")
                    if model == "<synthetic>":
                        continue

                    rows.append(
                        ui.UsageRow(
                            ts=ts,
                            provider=ui.infer_provider(model),
                            model=model,
                            project=project,
                            input_tokens=ui._int_field(usage.get("input_tokens", 0)),
                            output_tokens=ui._int_field(usage.get("output_tokens", 0)),
                            cache_read_tokens=ui._int_field(
                                usage.get("cache_read_input_tokens", 0)
                            ),
                            cache_creation_tokens=ui._int_field(
                                usage.get("cache_creation_input_tokens", 0)
                            ),
                        )
                    )
        except OSError:
            continue
    return rows

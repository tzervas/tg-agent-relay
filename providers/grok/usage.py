"""Grok local session usage collector (context-peak proxy)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def default_sessions_dir() -> str:
    return str(Path.home() / ".grok" / "sessions")


def _int_field(v: Any) -> int:
    try:
        return int(v)
    except TypeError, ValueError:
        return 0


def _parse_iso8601(raw: Any) -> int | None:
    if not raw:
        return None
    try:
        import datetime

        s = str(raw)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return int(datetime.datetime.fromisoformat(s).timestamp())
    except ValueError, TypeError:
        return None


def collect_usage(base: Path) -> list[Any]:
    """Collect UsageRow-compatible tuples from ~/.grok/sessions.

    Uses peak params._meta.totalTokens per session as a Declared proxy.
    Returns list of usage_ingest.UsageRow when available, else NamedTuple-like.
    """
    try:
        from usage_ingest import UsageRow, infer_provider
    except ImportError:
        # Allow running without path hacks
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
        from usage_ingest import UsageRow, infer_provider  # type: ignore

    rows: list[Any] = []
    try:
        workspace_dirs = [p for p in base.iterdir() if p.is_dir()]
    except OSError:
        return rows

    for workspace_dir in workspace_dirs:
        project = workspace_dir.name
        if len(project) > 48:
            project = project[:45] + "…"
        try:
            sid_dirs = [p for p in workspace_dir.iterdir() if p.is_dir()]
        except OSError:
            continue
        for sess in sid_dirs:
            model = "grok"
            ts = int(sess.stat().st_mtime) if sess.exists() else int(time.time())
            summary_path = sess / "summary.json"
            if summary_path.is_file():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(summary, dict):
                        model = str(
                            summary.get("current_model_id")
                            or (summary.get("info") or {}).get("model")
                            or model
                        )
                        for key in ("updated_at", "last_active_at", "created_at"):
                            t = _parse_iso8601(summary.get(key))
                            if t is not None:
                                ts = t
                                break
                except OSError, ValueError, TypeError:
                    pass
            signals_path = sess / "signals.json"
            if signals_path.is_file():
                try:
                    signals = json.loads(signals_path.read_text(encoding="utf-8", errors="replace"))
                    if isinstance(signals, dict):
                        model = str(signals.get("primaryModelId") or model)
                        models_used = signals.get("modelsUsed")
                        if isinstance(models_used, list) and models_used and model == "grok":
                            model = str(models_used[0])
                except OSError, ValueError, TypeError:
                    pass

            peak = 0
            updates_path = sess / "updates.jsonl"
            if updates_path.is_file():
                try:
                    with updates_path.open(encoding="utf-8", errors="replace") as f:
                        for line in f:
                            if "totalTokens" not in line:
                                continue
                            try:
                                obj = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            meta = (obj.get("params") or {}).get("_meta") or {}
                            if isinstance(meta, dict) and "totalTokens" in meta:
                                peak = max(peak, _int_field(meta.get("totalTokens")))
                except OSError:
                    pass

            if peak <= 0:
                continue
            rows.append(
                UsageRow(
                    ts=ts,
                    provider=infer_provider(model),
                    model=model,
                    project=project,
                    input_tokens=peak,
                    output_tokens=0,
                    cache_read_tokens=0,
                    cache_creation_tokens=0,
                )
            )
    return rows

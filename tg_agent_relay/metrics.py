"""emit_metric — append TSV lines to .metrics.log (parity with shell)."""

from __future__ import annotations

import os
import time
from pathlib import Path


def metrics_path(bridge_dir: Path | str | None = None) -> Path:
    if bridge_dir:
        return Path(bridge_dir) / ".metrics.log"
    env = os.environ.get("RELAY_METRICS_LOG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / ".metrics.log"


def emit_metric(
    source: str,
    event: str,
    detail: str = "",
    *,
    bridge_dir: Path | str | None = None,
) -> None:
    """Best-effort append: epoch\\tsource\\tevent\\tdetail\\n"""
    path = metrics_path(bridge_dir)
    line = f"{int(time.time())}\t{source}\t{event}\t{detail}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass

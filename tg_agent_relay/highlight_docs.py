"""Host-side code highlight documents for Python send path (issue #57).

Uses ``lib/code_highlight.render_code_html`` when pygments is available.
Never raises for normal input; skip-graceful when mode is off or render fails.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tg_agent_relay.config import cfg_get
from tg_agent_relay.metrics import emit_metric

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# Fence open: ```lang optional
_FENCE_OPEN = re.compile(r"^```([A-Za-z0-9_+.-]*)\s*$")
_FENCE_CLOSE = re.compile(r"^```\s*$")


@dataclass(frozen=True)
class CodeDocJob:
    path: Path
    lang: str
    caption: str


def _as_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("1", "true", "yes", "on")


def _as_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError) as _exc:
        return default


def extract_fenced_blocks(text: str) -> list[tuple[str, str]]:
    """Return list of (lang, body) for closed fences only."""
    lines = text.splitlines()
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        m = _FENCE_OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        lang = m.group(1) or ""
        body_lines: list[str] = []
        i += 1
        closed = False
        while i < len(lines):
            if _FENCE_CLOSE.match(lines[i]):
                closed = True
                i += 1
                break
            body_lines.append(lines[i])
            i += 1
        if closed and body_lines:
            out.append((lang, "\n".join(body_lines)))
        # unclosed: skip (inline box still in main message)
    return out


def build_code_doc_jobs(
    text: str,
    *,
    config: dict[str, Any] | None = None,
    bridge_dir: Path | str | None = None,
) -> list[CodeDocJob]:
    """Render HTML docs for fences when mode=html-doc. Empty list if off/skip."""
    cfg = config or {}
    mode = str(cfg_get(cfg, "code_highlight.mode", "inline-only") or "inline-only").lower()
    if mode != "html-doc":
        return []

    theme = str(cfg_get(cfg, "code_highlight.theme", "monokai") or "monokai")
    line_numbers = _as_bool(cfg_get(cfg, "code_highlight.line_numbers", False), False)
    max_lines = _as_int(cfg_get(cfg, "code_highlight.max_lines", 60), 60)
    keep_text = str(cfg_get(cfg, "code_highlight.keep_text", "caption") or "caption").lower()
    if keep_text not in ("caption", "none"):
        keep_text = "caption"

    blocks = extract_fenced_blocks(text)
    if not blocks:
        return []

    try:
        from code_highlight import render_code_html  # type: ignore
    except ImportError:
        emit_metric(
            "code_highlight",
            "fallback",
            "pygments/module unavailable - message contained a fenced block",
            bridge_dir=bridge_dir,
        )
        return []

    jobs: list[CodeDocJob] = []
    for lang, body in blocks:
        fd, name = tempfile.mkstemp(prefix="relay-code-doc-", suffix=".html")
        os.close(fd)
        out_path = Path(name)
        ok, reason = render_code_html(
            body,
            lang,
            str(out_path),
            theme=theme,
            line_numbers=line_numbers,
            max_lines=max_lines,
        )
        if not ok:
            emit_metric(
                "code_highlight",
                "fallback",
                f"lang={lang or 'plain'} reason={reason}",
                bridge_dir=bridge_dir,
            )
            with suppress_unlink(out_path):
                pass
            continue
        caption = ""
        if keep_text == "caption":
            # Match shell: pre/code box as caption if short enough
            from tg_agent_relay.format_api import format_message

            box = format_message(f"```{lang}\n{body}\n```", enabled=True).text
            if len(box) <= 1024:
                caption = box
        jobs.append(CodeDocJob(path=out_path, lang=lang or "plain", caption=caption))
        emit_metric(
            "code_highlight",
            "render",
            f"lang={lang or 'plain'} lines={body.count(chr(10)) + 1}",
            bridge_dir=bridge_dir,
        )
    return jobs


class suppress_unlink:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> None:
        with suppress(OSError):
            self.path.unlink(missing_ok=True)


def send_code_doc_jobs(
    token: str,
    chat_id: str,
    jobs: list[CodeDocJob],
    *,
    thread_id: str = "",
    send_document_fn: Any = None,
) -> int:
    """Send pending docs; delete temp files. Returns count sent."""
    from tg_agent_relay.send import send_document as _send_doc

    send_fn = send_document_fn or _send_doc
    n = 0
    for job in jobs:
        try:
            ok = send_fn(
                token,
                chat_id,
                job.path,
                caption=job.caption,
                thread_id=thread_id,
            )
            if ok:
                n += 1
                emit_metric("code_highlight", "sent", f"lang={job.lang}")
        finally:
            with suppress(OSError):
                job.path.unlink(missing_ok=True)
    return n

"""Outbound Telegram send — skeleton for issue #26.

Live path remains tg-send.sh until this module is complete. Swarm agents
must implement against SendRequest / Sender in protocols.py.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from tg_agent_relay.metrics import emit_metric
from tg_agent_relay.protocols import SendRequest


def load_env(bridge_dir: Path | str | None = None) -> dict[str, str]:
    """Parse KEY=VALUE from .env (no export/shell)."""
    root = Path(bridge_dir) if bridge_dir else Path(__file__).resolve().parents[1]
    path = root / ".env"
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip("'").strip('"')
    except OSError:
        pass
    return out


def _api_url(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def send_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    parse_mode: str = "",
    thread_id: str = "",
    timeout: float = 10.0,
) -> bool:
    """POST sendMessage. Returns True if ok:true. No raise on network fail."""
    data: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if thread_id:
        data["message_thread_id"] = thread_id
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        _api_url(token, "sendMessage"),
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return '"ok":true' in raw.replace(" ", "")
    except urllib.error.URLError, TimeoutError, OSError:
        return False


class EnvSender:
    """Minimal Sender: one message, no pagination/TTS (full port is #26)."""

    def __init__(self, bridge_dir: Path | str | None = None) -> None:
        self.bridge_dir = Path(bridge_dir) if bridge_dir else Path(__file__).resolve().parents[1]
        env = load_env(self.bridge_dir)
        self.token = env.get("BOT_TOKEN") or os.environ.get("BOT_TOKEN") or ""
        self.default_chat = env.get("ALLOWED_CHAT_ID") or os.environ.get("ALLOWED_CHAT_ID") or ""

    def send(self, req: SendRequest) -> None:
        chat = req.chat_id or self.default_chat
        if not self.token or not chat or not req.text:
            return
        ok = send_message(
            self.token,
            chat,
            req.text,
            parse_mode=req.parse_mode,
            thread_id=req.thread_id,
        )
        emit_metric(
            "tg-send-py",
            "send" if ok else "send_fail",
            f"pages=1 backend={req.backend}",
            bridge_dir=self.bridge_dir,
        )

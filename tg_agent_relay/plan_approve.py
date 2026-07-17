"""Pending plan storage + Telegram approve/reject keyboard (offline-safe)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

_APPROVE_TEXT = re.compile(
    r"^(?:approve|approved|lgtm|ship\s+it|yes|go)\b",
    re.I,
)
_REJECT_TEXT = re.compile(
    r"^(?:reject|rejected|no|stop|cancel)\b",
    re.I,
)
_CALLBACK = re.compile(r"^plan:(approve|reject|later):([0-9A-Za-z_-]+)$")
_LATEST_FILE = "latest-pending.json"


def _plans_dir(bridge_dir: Path | str) -> Path:
    return Path(bridge_dir) / ".plans"


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def new_plan_id(text: str) -> str:
    return f"{text_hash(text)}-{int(time.time())}"


def plan_record_path(bridge_dir: Path | str, plan_id: str) -> Path:
    safe = re.sub(r"[^0-9A-Za-z_.-]+", "_", plan_id)
    return _plans_dir(bridge_dir) / f"{safe}.json"


def store_pending_plan(
    bridge_dir: Path | str,
    text: str,
    *,
    plan_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Persist plan body; returns plan id."""
    root = Path(bridge_dir)
    plans = _plans_dir(root)
    plans.mkdir(parents=True, exist_ok=True)
    pid = plan_id or new_plan_id(text)
    body_hash = text_hash(text)
    record: dict[str, Any] = {
        "id": pid,
        "status": "pending",
        "text_hash": body_hash,
        "text": text,
        "created_at": int(time.time()),
        "meta": meta or {},
    }
    path = plan_record_path(root, pid)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (_plans_dir(root) / _LATEST_FILE).write_text(
        json.dumps({"id": pid, "updated_at": record["created_at"]}, indent=2) + "\n",
        encoding="utf-8",
    )
    return pid


def load_plan(bridge_dir: Path | str, plan_id: str) -> dict[str, Any] | None:
    path = plan_record_path(bridge_dir, plan_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def latest_pending_id(bridge_dir: Path | str) -> str:
    path = _plans_dir(bridge_dir) / _LATEST_FILE
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return str(data.get("id") or "")
    except (OSError, json.JSONDecodeError):
        pass
    return ""


def load_latest_pending(bridge_dir: Path | str) -> dict[str, Any] | None:
    pid = latest_pending_id(bridge_dir)
    if not pid:
        return None
    rec = load_plan(bridge_dir, pid)
    if rec and str(rec.get("status")) == "pending":
        return rec
    return None


def set_plan_status(bridge_dir: Path | str, plan_id: str, status: str) -> bool:
    rec = load_plan(bridge_dir, plan_id)
    if not rec:
        return False
    rec["status"] = status
    rec["updated_at"] = int(time.time())
    plan_record_path(bridge_dir, plan_id).write_text(
        json.dumps(rec, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return True


def build_inline_keyboard(plan_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve", "callback_data": f"plan:approve:{plan_id}"},
                {"text": "❌ Reject", "callback_data": f"plan:reject:{plan_id}"},
            ],
            [{"text": "⏳ Later", "callback_data": f"plan:later:{plan_id}"}],
        ]
    }


def reply_markup_json(plan_id: str) -> str:
    return json.dumps(build_inline_keyboard(plan_id), separators=(",", ":"))


def parse_callback_data(data: str) -> tuple[str, str] | None:
    m = _CALLBACK.match((data or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def parse_text_reply(text: str, *, bridge_dir: Path | str) -> tuple[str, str] | None:
    """Map free-text approval to (action, plan_id) for latest pending plan."""
    body = (text or "").strip()
    if not body:
        return None
    action = ""
    if _APPROVE_TEXT.match(body):
        action = "approve"
    elif _REJECT_TEXT.match(body):
        action = "reject"
    else:
        return None
    pid = latest_pending_id(bridge_dir)
    if not pid:
        return None
    return action, pid


def agent_emit_line(status: str, plan_id: str) -> str:
    return f"[telegram:plan] status={status} id={plan_id}"


def usage_keyboard_json() -> str:
    return json.dumps(
        {
            "inline_keyboard": [
                [
                    {"text": "24h", "callback_data": "usage:window:24h"},
                    {"text": "7d", "callback_data": "usage:window:7d"},
                    {"text": "30d", "callback_data": "usage:window:30d"},
                ],
                [{"text": "🔄 Refresh", "callback_data": "usage:window:refresh"}],
            ]
        },
        separators=(",", ":"),
    )


def usage_agent_cmd(window: str) -> str:
    w = (window or "7d").strip().lower()
    if w == "refresh":
        w = "7d"
    return f"[telegram:cmd:usage] window={w}"


def maybe_reply_markup_for_body(body: str, bridge_dir: Path | str) -> str | None:
    """When *body* classifies as PLAN, persist it and return keyboard JSON."""
    from tg_agent_relay.comms_format import MessageKind, classify_message

    if classify_message(body) != MessageKind.PLAN:
        return None
    pid = store_pending_plan(bridge_dir, body)
    return reply_markup_json(pid)
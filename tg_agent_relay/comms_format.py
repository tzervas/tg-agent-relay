"""Classify outbound relay messages and apply mobile-friendly templates + stamp."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from enum import StrEnum

from tg_agent_relay.agent_stamp import build_stamp

_GH_PR = re.compile(
    r"(https?://github\.com/[^\s/]+/[^\s/]+/pull/\d+)|"
    r"(\bopened\s+PR\b)|(\bPR\s*#\s*\d+)|(\breview\s+summary\b)",
    re.I,
)
_PLAN = re.compile(
    r"(WAVE_PLAN|L0\s+plan|mobile\s+plan|##\s*Plan\b|"
    r"\bplan\s+dump\b|\bimplementation\s+plan\b)",
    re.I,
)
_STOP_TEXT = re.compile(
    r"(permission\s+wait|need[\s-]human|waiting\s+for\s+(you|approval)|"
    r"human[\s-]input|approve\s+to\s+continue)",
    re.I,
)
_MERGED = re.compile(r"\b(merged|MERGE)\b")

_STOP_EVENTS = frozenset({"Stop", "StopFailure"})
_SUBAGENT_EVENTS = frozenset({"SubagentStop", "SubagentStart"})
_NOTIFY_EVENTS = frozenset({"Notification"})
_PERMISSION_EVENTS = frozenset({"PermissionDenied", "PermissionRequest"})

_KIND_HEADER: dict[str, str] = {
    "PR": "📣 PR",
    "PLAN": "📋 PLAN",
    "STOP": "🛑 STOP",
    "SUBAGENT": "🤖 SUBAGENT",
    "NOTIFY": "🔔 NOTIFY",
    "PERMISSION": "⏸ PERMISSION",
}


class MessageKind(StrEnum):
    PR = "PR"
    PLAN = "PLAN"
    STOP = "STOP"
    SUBAGENT = "SUBAGENT"
    NOTIFY = "NOTIFY"
    PERMISSION = "PERMISSION"
    GENERIC = "GENERIC"


def classify_message(text: str, hook_event: str | None = None) -> MessageKind:
    ev = (hook_event or os.environ.get("RELAY_HOOK_EVENT") or "").strip()
    if ev in _STOP_EVENTS:
        return MessageKind.STOP
    if ev in _SUBAGENT_EVENTS:
        return MessageKind.SUBAGENT
    if ev in _NOTIFY_EVENTS:
        return MessageKind.NOTIFY
    if ev in _PERMISSION_EVENTS:
        return MessageKind.PERMISSION

    body = text or ""
    if _GH_PR.search(body):
        return MessageKind.PR
    if _PLAN.search(body):
        return MessageKind.PLAN
    if _STOP_TEXT.search(body):
        return MessageKind.STOP
    if re.search(r"\bsubagent\b", body, re.I):
        return MessageKind.SUBAGENT
    return MessageKind.GENERIC


def _normalize_body(text: str) -> str:
    lines = [ln.rstrip() for ln in (text or "").splitlines()]
    out: list[str] = []
    prev_blank = False
    for ln in lines:
        blank = not ln.strip()
        if blank and prev_blank:
            continue
        out.append(ln)
        prev_blank = blank
    return "\n".join(out).strip()


def format_outbound(
    body: str,
    *,
    hook_event: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    skip_stamp: bool = False,
) -> str:
    raw = (body or "").strip()
    if not raw:
        return raw

    if raw.startswith("🏷 repo=") or "\n🏷 repo=" in raw:
        return raw
    for hdr in _KIND_HEADER.values():
        if raw.startswith(hdr):
            return raw

    kind = classify_message(raw, hook_event)
    merged_hint = bool(_MERGED.search(raw))
    stamp = ""
    add_stamp = not skip_stamp and (bool(hook_event) or kind != MessageKind.GENERIC)
    if add_stamp:
        stamp = build_stamp(
            cwd=cwd or os.environ.get("RELAY_CWD"), env=env, body_for_merge_hint=raw
        )
        if merged_hint and stamp and "status=merged" not in stamp:
            stamp = stamp + ("\n" if stamp else "") + "📌 status=merged"

    normalized = _normalize_body(raw)
    parts: list[str] = []
    header = _KIND_HEADER.get(kind.value, "")
    if header and kind != MessageKind.GENERIC:
        parts.append(header)
    parts.append(normalized)
    if stamp:
        parts.append(stamp)
    return "\n".join(p for p in parts if p)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Format outbound comms message")
    p.add_argument("--hook-event", default="")
    p.add_argument("--cwd", default="")
    p.add_argument("--skip-stamp", action="store_true")
    p.add_argument("text", nargs="?", default="")
    args = p.parse_args(argv)
    msg = args.text or sys.stdin.read()
    out = format_outbound(
        msg,
        hook_event=args.hook_event or None,
        cwd=args.cwd or None,
        skip_stamp=args.skip_stamp,
    )
    print(out, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

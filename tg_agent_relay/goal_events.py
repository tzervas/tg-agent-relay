"""Detect benign goal-mode tool noise and soften outbound hook spam."""

from __future__ import annotations

import re
from typing import Literal

GoalNoiseAction = Literal["skip", "soft", "pass"]

_GOAL_FAIL = re.compile(
    r"(update_goal\s+failed|Goal\s+is\s+not\s+Active|cannot\s+mark\s+complete|"
    r"goal\s+is\s+not\s+active)",
    re.I,
)
_UPDATE_GOAL_TOOL = re.compile(r"update_goal", re.I)

_SOFT_LINE = "ℹ️ Goal idle (no active goal to update)."


def is_goal_noise_text(text: str) -> bool:
    """True when *text* looks like a benign update_goal / goal-state failure."""
    body = (text or "").strip()
    if not body:
        return False
    return bool(_GOAL_FAIL.search(body))


def is_update_goal_tool(tool_name: str) -> bool:
    return bool(_UPDATE_GOAL_TOOL.search(tool_name or ""))


def classify_goal_noise(
    text: str,
    *,
    tool_name: str = "",
    hook_event: str | None = None,
) -> GoalNoiseAction:
    """Return skip (suppress), soft (one-liner), or pass (unchanged)."""
    body = (text or "").strip()
    if not body and not tool_name:
        return "pass"

    ev = (hook_event or "").strip()
    if (
        ev == "PostToolUseFailure"
        and is_update_goal_tool(tool_name)
        and (is_goal_noise_text(body) or not body)
    ):
        return "skip"

    if is_goal_noise_text(body):
        if ev in ("PostToolUseFailure", "Notification", "Stop", "StopFailure"):
            return "skip"
        if ev or "update_goal" in body.lower():
            return "soft"
    return "pass"


def apply_goal_noise_policy(
    text: str,
    *,
    tool_name: str = "",
    hook_event: str | None = None,
    is_hook: bool = False,
) -> str | None:
    """Filter outbound text. ``None`` means do not send."""
    action = classify_goal_noise(text, tool_name=tool_name, hook_event=hook_event)
    if action == "skip":
        return None
    if action == "soft":
        return _SOFT_LINE
    if is_hook and is_goal_noise_text(text):
        return None
    return text


def filter_hook_summary(
    summary: str,
    *,
    tool_name: str = "",
    hook_event: str | None = None,
) -> str | None:
    """Adapter/provider hook one-liner filter (``None`` → SKIP)."""
    return apply_goal_noise_policy(
        summary,
        tool_name=tool_name,
        hook_event=hook_event,
        is_hook=True,
    )

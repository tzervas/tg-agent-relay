"""Grok Build hook catalog + payload → summary formatting.

Canonical event set from ~/.grok/docs/user-guide/10-hooks.md (all 14).
Also accepts Cursor camelCase aliases and snake_case GROK_HOOK_EVENT values.
"""

from __future__ import annotations

import json
import re
from typing import Any

from providers.base import HookEvent

EVENTS: list[HookEvent] = [
    HookEvent(
        "SessionStart",
        False,
        "🟢",
        "Session starts",
        ("prefix", "event", "cwd", "session_id", "detail_suffix"),
    ),
    HookEvent(
        "UserPromptSubmit",
        False,
        "⌨️",
        "User submits a prompt",
        ("prefix", "event", "prompt", "message", "detail_suffix"),
    ),
    HookEvent(
        "PreToolUse",
        False,
        "🔧",
        "Tool about to run (blocking capable; we notify only)",
        ("prefix", "event", "tool", "verb", "tool_input"),
    ),
    HookEvent(
        "PostToolUse",
        False,
        "🔧",
        "Tool completed successfully",
        ("prefix", "event", "tool", "verb", "tool_input"),
    ),
    HookEvent(
        "PostToolUseFailure",
        True,
        "⚠️",
        "Tool failed",
        ("prefix", "event", "tool", "message", "detail_suffix"),
    ),
    HookEvent(
        "PermissionDenied",
        False,
        "🚫",
        "Permission system denied a tool",
        ("prefix", "event", "tool"),
    ),
    HookEvent(
        "Stop",
        True,
        "🏁",
        "Agent turn ended",
        ("prefix", "event", "message", "detail_suffix", "stop_reason"),
    ),
    HookEvent(
        "StopFailure", True, "🛑", "Turn ended due to API error", ("prefix", "event", "error_type")
    ),
    HookEvent(
        "Notification",
        True,
        "🔔",
        "Agent notification",
        ("prefix", "event", "notification_type", "message", "detail_suffix"),
    ),
    HookEvent(
        "SubagentStart", False, "🚀", "Subagent started", ("prefix", "event", "agent", "agent_id")
    ),
    HookEvent(
        "SubagentStop",
        True,
        "✅",
        "Subagent finished",
        ("prefix", "event", "agent", "message", "detail_suffix"),
    ),
    HookEvent("PreCompact", False, "🗜️", "Compaction about to run", ("prefix", "event", "trigger")),
    HookEvent("PostCompact", False, "📦", "Compaction finished", ("prefix", "event", "trigger")),
    HookEvent("SessionEnd", False, "🔴", "Session ended", ("prefix", "event", "reason")),
]

_CANON = {e.name for e in EVENTS}

# Cursor + snake + alias map → canonical
_ALIASES: dict[str, str] = {
    "sessionstart": "SessionStart",
    "session_start": "SessionStart",
    "userpromptsubmit": "UserPromptSubmit",
    "user_prompt_submit": "UserPromptSubmit",
    "beforesubmitprompt": "UserPromptSubmit",
    "pretooluse": "PreToolUse",
    "pre_tool_use": "PreToolUse",
    "beforeshellexecution": "PreToolUse",
    "beforemcpexecution": "PreToolUse",
    "beforereadfile": "PreToolUse",
    "posttooluse": "PostToolUse",
    "post_tool_use": "PostToolUse",
    "aftershellexecution": "PostToolUse",
    "aftermcpexecution": "PostToolUse",
    "afterfileedit": "PostToolUse",
    "afteragentresponse": "PostToolUse",
    "afteragentthought": "PostToolUse",
    "posttoolusefailure": "PostToolUseFailure",
    "post_tool_use_failure": "PostToolUseFailure",
    "permissiondenied": "PermissionDenied",
    "permission_denied": "PermissionDenied",
    "stop": "Stop",
    "stopfailure": "StopFailure",
    "stop_failure": "StopFailure",
    "notification": "Notification",
    "subagentstart": "SubagentStart",
    "subagent_start": "SubagentStart",
    "subagentstop": "SubagentStop",
    "subagent_stop": "SubagentStop",
    "subagentend": "SubagentStop",
    "subagent_end": "SubagentStop",
    "precompact": "PreCompact",
    "pre_compact": "PreCompact",
    "postcompact": "PostCompact",
    "post_compact": "PostCompact",
    "sessionend": "SessionEnd",
    "session_end": "SessionEnd",
}


def normalize_event(raw: str) -> str:
    raw = (raw or "").strip()
    if raw in _CANON:
        return raw
    if raw == "SubagentEnd":
        return "SubagentStop"
    key = raw.lower().replace("-", "_")
    # also try without underscores
    if key in _ALIASES:
        return _ALIASES[key]
    compact = key.replace("_", "")
    if compact in _ALIASES:
        return _ALIASES[compact]
    # snake_case → PascalCase best-effort
    if "_" in key:
        return "".join(p.capitalize() for p in key.split("_") if p)
    return raw or "unknown"


def _g(payload: dict[str, Any], *keys: str, default: str = "") -> str:
    for k in keys:
        if k in payload and payload[k] is not None and payload[k] != "":
            v = payload[k]
            if isinstance(v, (dict, list)):
                try:
                    s = json.dumps(v, ensure_ascii=False)
                except TypeError, ValueError:
                    s = str(v)
            else:
                s = str(v)
            if s and s != "null":
                return s
    return default


def _oneline(s: str, limit: int = 0) -> str:
    s = re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()
    if limit and len(s) > limit:
        return s[: limit - 3] + "..."
    return s


def _render(template: str, **kw: str) -> str:
    """Simple {placeholder} substitution; unknown keys left literal."""
    out = template
    for k, v in kw.items():
        out = out.replace("{" + k + "}", v)
    return out


def format_hook(payload: dict[str, Any], event: str, opts: dict[str, str]) -> str:
    """Build one-line summary. opts may include prefix, format (custom template)."""
    prefix = opts.get("prefix") or next((e.default_prefix for e in EVENTS if e.name == event), "ℹ️")
    custom = (opts.get("format") or "").strip()

    session_id = _g(payload, "sessionId", "session_id")
    cwd = _g(payload, "cwd", "workspaceRoot", "workspace_root")
    tool = _g(payload, "toolName", "tool_name", default="tool")
    tool_input = _oneline(_g(payload, "toolInput", "tool_input"), 120)
    tool_use_id = _g(payload, "toolUseId", "tool_use_id")

    base_kw = {
        "prefix": prefix,
        "event": event,
        "session_id": session_id,
        "cwd": cwd,
        "tool": tool,
        "tool_input": tool_input,
        "tool_use_id": tool_use_id,
    }

    if event == "SessionStart":
        detail = f" in {cwd}" if cwd else ""
        tmpl = custom or "{prefix} Grok session started{detail_suffix}"
        return _render(tmpl, **base_kw, detail_suffix=detail)

    if event == "UserPromptSubmit":
        prompt = _oneline(_g(payload, "prompt", "text", "content"), 200)
        detail = f": {prompt}" if prompt else ""
        tmpl = custom or "{prefix} prompt submitted{detail_suffix}"
        return _render(tmpl, **base_kw, prompt=prompt, message=prompt, detail_suffix=detail)

    if event in ("PreToolUse", "PostToolUse"):
        verb = "using" if event == "PreToolUse" else "used"
        tmpl = custom or "{prefix} {verb} {tool}"
        return _render(tmpl, **base_kw, verb=verb)

    if event == "PostToolUseFailure":
        err = _oneline(_g(payload, "error", "error_message", "message", "errorMessage"), 200)
        detail = f": {err}" if err else ""
        tmpl = custom or "{prefix} {tool} failed{detail_suffix}"
        return _render(tmpl, **base_kw, message=err, detail_suffix=detail)

    if event == "PermissionDenied":
        reason = _oneline(_g(payload, "reason", "message"), 120)
        tmpl = custom or ("{prefix} {tool} denied" + (": {reason}" if reason else ""))
        return _render(tmpl, **base_kw, reason=reason)

    if event == "Stop":
        msg = _oneline(
            _g(payload, "last_assistant_message", "message", "reason", "stop_reason", "stopReason"),
            300,
        )
        stop_reason = _g(payload, "stop_reason", "stopReason", "reason", default="end_turn")
        detail = f" — {msg}" if msg else ""
        tmpl = custom or "{prefix} Grok turn finished{detail_suffix}"
        return _render(tmpl, **base_kw, message=msg, detail_suffix=detail, stop_reason=stop_reason)

    if event == "StopFailure":
        et = _g(payload, "error_type", "errorType", "error", "reason", default="error")
        tmpl = custom or "{prefix} Grok turn error: {error_type}"
        return _render(tmpl, **base_kw, error_type=et)

    if event == "Notification":
        ntype = _g(payload, "notification_type", "notificationType", "type", default="notice")
        msg = _oneline(_g(payload, "message", "text"), 200)
        detail = f": {msg}" if msg else ""
        tmpl = custom or "{prefix} {notification_type}{detail_suffix}"
        return _render(tmpl, **base_kw, notification_type=ntype, message=msg, detail_suffix=detail)

    if event == "SubagentStart":
        agent = _g(
            payload,
            "agent_type",
            "agentType",
            "subagent_type",
            "subagentType",
            "name",
            default="subagent",
        )
        agent_id = _g(payload, "agent_id", "agentId", "id")
        tmpl = custom or "{prefix} {agent} started"
        return _render(tmpl, **base_kw, agent=agent, agent_id=agent_id)

    if event == "SubagentStop":
        agent = _g(
            payload,
            "agent_type",
            "agentType",
            "subagent_type",
            "subagentType",
            "name",
            default="subagent",
        )
        msg = _oneline(_g(payload, "last_assistant_message", "message"), 300)
        detail = f" — {msg}" if msg else ""
        tmpl = custom or "{prefix} {agent} finished{detail_suffix}"
        return _render(tmpl, **base_kw, agent=agent, message=msg, detail_suffix=detail)

    if event == "PreCompact":
        trigger = _g(payload, "trigger", default="auto")
        tmpl = custom or "{prefix} context compacting ({trigger})"
        return _render(tmpl, **base_kw, trigger=trigger)

    if event == "PostCompact":
        trigger = _g(payload, "trigger", default="auto")
        tmpl = custom or "{prefix} context compaction finished ({trigger})"
        return _render(tmpl, **base_kw, trigger=trigger)

    if event == "SessionEnd":
        reason = _g(payload, "reason", "end_reason", "endReason", default="unknown")
        tmpl = custom or "{prefix} Grok session ended ({reason})"
        return _render(tmpl, **base_kw, reason=reason)

    tmpl = custom or "{prefix} Grok event: {event}"
    return _render(tmpl, **base_kw)

"""Grok Build hook catalog + payload → summary formatting.

Canonical event set from ~/.grok/docs/user-guide/10-hooks.md (all 14).
Also accepts Cursor camelCase aliases and snake_case GROK_HOOK_EVENT values.

Phone-facing one-liners mirror Claude's tone (prefix + useful detail, no
multi-line spam). Custom templates use ``[grok.<Event>].format`` with the
placeholders declared on each :class:`~providers.base.HookEvent`.
"""

from __future__ import annotations

import json
import re
from typing import Any

from providers.base import HookEvent

# Default max length for optional tool_input snippets in summaries.
_DEFAULT_TOOL_INPUT_LIMIT = 120
_DEFAULT_MSG_LIMIT = 300
_DEFAULT_PROMPT_LIMIT = 200
_DEFAULT_ERR_LIMIT = 200

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
        ("prefix", "event", "tool", "verb", "tool_input", "detail_suffix"),
    ),
    HookEvent(
        "PostToolUse",
        False,
        "🔧",
        "Tool completed successfully",
        ("prefix", "event", "tool", "verb", "tool_input", "detail_suffix"),
    ),
    HookEvent(
        "PostToolUseFailure",
        True,
        "⚠️",
        "Tool failed",
        ("prefix", "event", "tool", "message", "tool_input", "detail_suffix"),
    ),
    HookEvent(
        "PermissionDenied",
        False,
        "🚫",
        "Permission system denied a tool",
        ("prefix", "event", "tool", "reason", "detail_suffix"),
    ),
    HookEvent(
        "Stop",
        True,
        "🏁",
        "Agent turn ended",
        ("prefix", "event", "message", "detail_suffix", "stop_reason"),
    ),
    HookEvent(
        "StopFailure",
        True,
        "🛑",
        "Turn ended due to API error",
        ("prefix", "event", "error_type", "message", "detail_suffix"),
    ),
    HookEvent(
        "Notification",
        True,
        "🔔",
        "Agent notification",
        ("prefix", "event", "notification_type", "message", "detail_suffix"),
    ),
    HookEvent(
        "SubagentStart",
        False,
        "🚀",
        "Subagent started",
        ("prefix", "event", "agent", "agent_id"),
    ),
    HookEvent(
        "SubagentStop",
        True,
        "✅",
        "Subagent finished",
        ("prefix", "event", "agent", "message", "detail_suffix"),
    ),
    HookEvent(
        "PreCompact",
        False,
        "🗜️",
        "Compaction about to run",
        ("prefix", "event", "trigger"),
    ),
    HookEvent(
        "PostCompact",
        False,
        "📦",
        "Compaction finished",
        ("prefix", "event", "trigger"),
    ),
    HookEvent(
        "SessionEnd",
        False,
        "🔴",
        "Session ended",
        ("prefix", "event", "reason"),
    ),
]

_CANON = {e.name for e in EVENTS}

# Cursor + snake + alias map → canonical PascalCase event names.
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
    """Map raw harness / env event names to canonical PascalCase.

    Args:
        raw: Value from ``hookEventName``, ``hook_event_name``, or
            ``GROK_HOOK_EVENT``. Accepts Cursor camelCase and snake_case.

    Returns:
        Canonical event name when known; best-effort PascalCase or ``unknown``
        for empty input.
    """
    raw = (raw or "").strip()
    if not raw:
        return "unknown"
    if raw in _CANON:
        return raw
    if raw == "SubagentEnd":
        return "SubagentStop"
    key = raw.lower().replace("-", "_")
    if key in _ALIASES:
        return _ALIASES[key]
    compact = key.replace("_", "")
    if compact in _ALIASES:
        return _ALIASES[compact]
    # snake_case → PascalCase best-effort
    if "_" in key:
        return "".join(p.capitalize() for p in key.split("_") if p)
    # camelCase already mixed
    if raw[0].islower() and any(c.isupper() for c in raw[1:]):
        return raw[0].upper() + raw[1:]
    return raw


def _g(payload: dict[str, Any], *keys: str, default: str = "") -> str:
    """Return the first non-empty string for any of ``keys`` in ``payload``.

    Dict/list values are JSON-serialized so they can appear in one-liners.
    """
    for k in keys:
        if k in payload and payload[k] is not None and payload[k] != "":
            v = payload[k]
            if isinstance(v, (dict, list)):
                try:
                    s = json.dumps(v, ensure_ascii=False)
                except (TypeError, ValueError) as _exc:
                    s = str(v)
            else:
                s = str(v)
            if s and s != "null":
                return s
    return default


def _oneline(s: str, limit: int = 0) -> str:
    """Collapse whitespace/newlines; optionally truncate with ellipsis."""
    s = re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()
    if limit and len(s) > limit:
        return s[: limit - 3] + "..."
    return s


def _render(template: str, **kw: str) -> str:
    """Simple ``{placeholder}`` substitution; unknown keys left literal."""
    out = template
    for k, v in kw.items():
        out = out.replace("{" + k + "}", v)
    return out


def _int_opt(opts: dict[str, str], *keys: str, default: int) -> int:
    """Parse a positive int from opts; fall back to ``default`` on bad input."""
    for k in keys:
        raw = opts.get(k)
        if raw is None or raw == "":
            continue
        try:
            n = int(raw)
        except (TypeError, ValueError) as _exc:
            continue
        if n > 0:
            return n
    return default


def _agent_name(payload: dict[str, Any], default: str = "subagent") -> str:
    return _g(
        payload,
        "agent_type",
        "agentType",
        "subagent_type",
        "subagentType",
        "name",
        default=default,
    )


def format_hook(payload: dict[str, Any], event: str, opts: dict[str, str]) -> str:
    """Build a single-line phone summary for a Grok hook event.

    Args:
        payload: Hook JSON object (camelCase and/or snake_case fields).
        event: Canonical event name (see :func:`normalize_event`).
        opts: Resolved config. Recognized keys:
            ``prefix`` — emoji/text prefix (falls back to catalog default).
            ``format`` — custom ``{placeholder}`` template for this event.
            ``tool_input_limit`` / ``input_limit`` — max chars for tool input
            snippets (default 120).

    Returns:
        One-line summary with collapsed whitespace (never multi-line).
    """
    prefix = opts.get("prefix") or next((e.default_prefix for e in EVENTS if e.name == event), "ℹ️")
    custom = (opts.get("format") or "").strip()
    tin_limit = _int_opt(opts, "tool_input_limit", "input_limit", default=_DEFAULT_TOOL_INPUT_LIMIT)

    session_id = _g(payload, "sessionId", "session_id")
    cwd = _g(payload, "cwd", "workspaceRoot", "workspace_root")
    tool = _g(payload, "toolName", "tool_name", default="tool")
    tool_input = _oneline(_g(payload, "toolInput", "tool_input"), tin_limit)
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
        line = _render(tmpl, **base_kw, detail_suffix=detail)

    elif event == "UserPromptSubmit":
        prompt = _oneline(_g(payload, "prompt", "text", "content"), _DEFAULT_PROMPT_LIMIT)
        detail = f": {prompt}" if prompt else ""
        tmpl = custom or "{prefix} prompt submitted{detail_suffix}"
        line = _render(tmpl, **base_kw, prompt=prompt, message=prompt, detail_suffix=detail)

    elif event in ("PreToolUse", "PostToolUse"):
        # Tool name always; optional truncated input when present (phone-useful).
        verb = "using" if event == "PreToolUse" else "used"
        detail = f": {tool_input}" if tool_input else ""
        tmpl = custom or "{prefix} {verb} {tool}{detail_suffix}"
        line = _render(tmpl, **base_kw, verb=verb, detail_suffix=detail)

    elif event == "PostToolUseFailure":
        # Prefer explicit error fields so failures surface on the phone.
        err = _oneline(
            _g(payload, "error", "error_message", "errorMessage", "message"),
            _DEFAULT_ERR_LIMIT,
        )
        detail = f": {err}" if err else ""
        tmpl = custom or "{prefix} {tool} failed{detail_suffix}"
        line = _render(tmpl, **base_kw, message=err, detail_suffix=detail)

    elif event == "PermissionDenied":
        reason = _oneline(_g(payload, "reason", "message"), 120)
        detail = f": {reason}" if reason else ""
        tmpl = custom or "{prefix} {tool} denied{detail_suffix}"
        line = _render(tmpl, **base_kw, reason=reason, detail_suffix=detail)

    elif event == "Stop":
        # Prefer last assistant text; do not treat stop_reason as the message.
        msg = _oneline(
            _g(payload, "last_assistant_message", "message"),
            _DEFAULT_MSG_LIMIT,
        )
        stop_reason = _g(payload, "stop_reason", "stopReason", "reason", default="end_turn")
        detail = f" — {msg}" if msg else ""
        tmpl = custom or "{prefix} Grok turn finished{detail_suffix}"
        line = _render(
            tmpl,
            **base_kw,
            message=msg,
            detail_suffix=detail,
            stop_reason=stop_reason,
        )

    elif event == "StopFailure":
        et = _g(payload, "error_type", "errorType", "error", "reason", default="error")
        msg = _oneline(_g(payload, "message", "error_message", "errorMessage"), 200)
        detail = f": {msg}" if msg else ""
        # Default keeps error_type primary (short); message via custom template.
        tmpl = custom or "{prefix} Grok turn error: {error_type}"
        line = _render(tmpl, **base_kw, error_type=et, message=msg, detail_suffix=detail)

    elif event == "Notification":
        ntype = _g(
            payload,
            "notification_type",
            "notificationType",
            "type",
            default="notice",
        )
        msg = _oneline(_g(payload, "message", "text"), 200)
        detail = f": {msg}" if msg else ""
        tmpl = custom or "{prefix} {notification_type}{detail_suffix}"
        line = _render(
            tmpl,
            **base_kw,
            notification_type=ntype,
            message=msg,
            detail_suffix=detail,
        )

    elif event == "SubagentStart":
        agent = _agent_name(payload)
        agent_id = _g(payload, "agent_id", "agentId", "id")
        tmpl = custom or "{prefix} {agent} started"
        line = _render(tmpl, **base_kw, agent=agent, agent_id=agent_id)

    elif event == "SubagentStop":
        agent = _agent_name(payload)
        msg = _oneline(
            _g(payload, "last_assistant_message", "message"),
            _DEFAULT_MSG_LIMIT,
        )
        detail = f" — {msg}" if msg else ""
        tmpl = custom or "{prefix} {agent} finished{detail_suffix}"
        line = _render(tmpl, **base_kw, agent=agent, message=msg, detail_suffix=detail)

    elif event == "PreCompact":
        trigger = _g(payload, "trigger", "compaction_trigger", default="auto")
        tmpl = custom or "{prefix} context compacting ({trigger})"
        line = _render(tmpl, **base_kw, trigger=trigger)

    elif event == "PostCompact":
        trigger = _g(payload, "trigger", default="auto")
        tmpl = custom or "{prefix} context compaction finished ({trigger})"
        line = _render(tmpl, **base_kw, trigger=trigger)

    elif event == "SessionEnd":
        reason = _g(payload, "reason", "end_reason", "endReason", default="unknown")
        tmpl = custom or "{prefix} Grok session ended ({reason})"
        line = _render(tmpl, **base_kw, reason=reason)

    else:
        tmpl = custom or "{prefix} Grok event: {event}"
        line = _render(tmpl, **base_kw)

    # Final pass: collapse any accidental newlines from payload-derived text.
    return _oneline(line)

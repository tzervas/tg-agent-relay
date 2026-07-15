"""Claude Code hook catalog helpers + payload → summary formatting.

Canonical event set matches lib/claude-code-events.sh / adapters/claude-code.sh
(all 30 documented events). Field names are snake_case as in Claude Code hook
payloads (see adapters/claude-code.sh header).
"""

from __future__ import annotations

import json
import re
from typing import Any

from providers.base import HookEvent

# Full Claude Code event set (mirrors lib/claude-code-events.sh). Keep
# default_enabled / default_prefix in sync with that file and install defaults.
_EVENTS = [
    ("SessionStart", False, "🟢"),
    ("Setup", False, "⚙️"),
    ("UserPromptSubmit", False, "⌨️"),
    ("UserPromptExpansion", False, "🧩"),
    ("PreToolUse", False, "🔧"),
    ("PostToolUse", False, "🔧"),
    ("PostToolUseFailure", True, "⚠️"),
    ("PostToolBatch", False, "📦"),
    ("PermissionRequest", False, "🔐"),
    ("PermissionDenied", False, "🚫"),
    ("Stop", True, "🏁"),
    ("StopFailure", True, "🛑"),
    ("SubagentStart", False, "🚀"),
    ("SubagentStop", True, "✅"),
    ("TeammateIdle", False, "💤"),
    ("TaskCreated", False, "📋"),
    ("TaskCompleted", False, "☑️"),
    ("ConfigChange", False, "⚙️"),
    ("CwdChanged", False, "📂"),
    ("FileChanged", False, "📝"),
    ("InstructionsLoaded", False, "📖"),
    ("PreCompact", False, "🗜️"),
    ("PostCompact", False, "📦"),
    ("WorktreeCreate", False, "🌳"),
    ("WorktreeRemove", False, "🪓"),
    ("Elicitation", False, "❓"),
    ("ElicitationResult", False, "✔️"),
    ("Notification", True, "🔔"),
    ("MessageDisplay", False, "💬"),
    ("SessionEnd", False, "🔴"),
]

EVENTS = [
    HookEvent(name=n, default_enabled=en, default_prefix=px, description=f"Claude Code {n}")
    for n, en, px in _EVENTS
]

_CANON = {e.name for e in EVENTS}

# Lowercase / snake_case / compact aliases → canonical PascalCase
_ALIASES: dict[str, str] = {}
for _name in _CANON:
    low = _name.lower()
    _ALIASES[low] = _name
    # SessionStart → session_start
    snake = re.sub(r"([A-Z])", r"_\1", _name).lstrip("_").lower()
    _ALIASES[snake] = _name
    _ALIASES[low.replace("_", "")] = _name


def normalize_event(raw: str) -> str:
    """Return PascalCase event name; unknown values pass through (stripped)."""
    raw = (raw or "").strip()
    if not raw:
        return "unknown"
    if raw in _CANON:
        return raw
    key = raw.lower().replace("-", "_")
    if key in _ALIASES:
        return _ALIASES[key]
    compact = key.replace("_", "")
    if compact in _ALIASES:
        return _ALIASES[compact]
    if "_" in key:
        return "".join(p.capitalize() for p in key.split("_") if p)
    # camelCase / already mixed — title-case first letter groups best-effort
    if raw[0].islower() and any(c.isupper() for c in raw[1:]):
        return raw[0].upper() + raw[1:]
    return raw


def _g(payload: dict[str, Any], *keys: str, default: str = "") -> str:
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


def _nested(payload: dict[str, Any], *path: str, default: str = "") -> str:
    cur: Any = payload
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    if cur is None or cur == "":
        return default
    if isinstance(cur, (dict, list)):
        try:
            s = json.dumps(cur, ensure_ascii=False)
        except (TypeError, ValueError) as _exc:
            s = str(cur)
    else:
        s = str(cur)
    return s if s and s != "null" else default


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

    base_kw = {
        "prefix": prefix,
        "event": event,
    }

    if event == "SessionStart":
        source = _g(payload, "source", default="startup")
        model = _g(payload, "model")
        title = _g(payload, "session_title")
        agent = _g(payload, "agent_type")
        tmpl = custom or "{prefix} session started ({source})"
        return _render(
            tmpl,
            **base_kw,
            source=source,
            model=model,
            session_title=title,
            agent=agent,
        )

    if event == "Setup":
        source = _g(payload, "source", default="init")
        tmpl = custom or "{prefix} setup ({source})"
        return _render(tmpl, **base_kw, source=source)

    if event == "UserPromptSubmit":
        prompt = _oneline(_g(payload, "prompt"), 200)
        detail = f": {prompt}" if prompt else ""
        tmpl = custom or "{prefix} prompt submitted{detail_suffix}"
        return _render(tmpl, **base_kw, prompt=prompt, message=prompt, detail_suffix=detail)

    if event == "UserPromptExpansion":
        snippet = _oneline(_g(payload, "command", "prompt"), 200)
        detail = f": {snippet}" if snippet else ""
        tmpl = custom or "{prefix} prompt expansion{detail_suffix}"
        return _render(tmpl, **base_kw, message=snippet, detail_suffix=detail)

    if event in ("PreToolUse", "PostToolUse"):
        tool = _g(payload, "tool_name", "toolName", default="tool")
        verb = "using" if event == "PreToolUse" else "used"
        tmpl = custom or "{prefix} {verb} {tool}"
        return _render(tmpl, **base_kw, tool=tool, verb=verb)

    if event == "PostToolUseFailure":
        tool = _g(payload, "tool_name", "toolName", default="tool")
        err = _oneline(_g(payload, "error_message", "error", "message"), 200)
        detail = f": {err}" if err else ""
        tmpl = custom or "{prefix} {tool} failed{detail_suffix}"
        return _render(tmpl, **base_kw, tool=tool, message=err, detail_suffix=detail)

    if event == "PostToolBatch":
        tmpl = custom or "{prefix} tool batch completed"
        return _render(tmpl, **base_kw)

    if event == "PermissionRequest":
        tool = _g(payload, "tool_name", "toolName", default="tool")
        tmpl = custom or "{prefix} permission requested for {tool}"
        return _render(tmpl, **base_kw, tool=tool)

    if event == "PermissionDenied":
        tool = _g(payload, "tool_name", "toolName", default="tool")
        tmpl = custom or "{prefix} {tool} denied"
        return _render(tmpl, **base_kw, tool=tool)

    if event == "Stop":
        msg = _oneline(_g(payload, "last_assistant_message", "message"), 300)
        detail = f" — {msg}" if msg else ""
        tmpl = custom or "{prefix} session turn finished{detail_suffix}"
        return _render(tmpl, **base_kw, message=msg, detail_suffix=detail)

    if event == "StopFailure":
        et = _g(payload, "error_type", "errorType", "error", "reason", default="error")
        tmpl = custom or "{prefix} turn ended in error: {error_type}"
        return _render(tmpl, **base_kw, error_type=et)

    if event == "SubagentStart":
        agent = _g(payload, "agent_type", "agentType", default="agent")
        agent_id = _g(payload, "agent_id", "agentId")
        tmpl = custom or "{prefix} {agent} started"
        return _render(tmpl, **base_kw, agent=agent, agent_id=agent_id)

    if event == "SubagentStop":
        agent = _g(payload, "agent_type", "agentType", default="agent")
        msg = _oneline(_g(payload, "last_assistant_message", "message"), 300)
        detail = f" — {msg}" if msg else ""
        tmpl = custom or "{prefix} {agent} finished{detail_suffix}"
        return _render(tmpl, **base_kw, agent=agent, message=msg, detail_suffix=detail)

    if event == "TeammateIdle":
        tmpl = custom or "{prefix} teammate idle"
        return _render(tmpl, **base_kw)

    if event in ("TaskCreated", "TaskCompleted"):
        snippet = _oneline(_g(payload, "task", "description", "title"), 200)
        detail = f": {snippet}" if snippet else ""
        verb = "created" if event == "TaskCreated" else "completed"
        tmpl = custom or "{prefix} task {verb}{detail_suffix}"
        return _render(tmpl, **base_kw, verb=verb, message=snippet, detail_suffix=detail)

    if event == "ConfigChange":
        source = _g(payload, "source", default="settings")
        file_path = _g(payload, "file_path", "filePath")
        detail = f": {file_path}" if file_path else ""
        tmpl = custom or "{prefix} config changed ({source}){detail_suffix}"
        return _render(tmpl, **base_kw, source=source, file=file_path, detail_suffix=detail)

    if event == "CwdChanged":
        cwd = _g(payload, "cwd", "new_cwd", "newCwd")
        detail = f": {cwd}" if cwd else ""
        tmpl = custom or "{prefix} working directory changed{detail_suffix}"
        return _render(tmpl, **base_kw, cwd=cwd, detail_suffix=detail)

    if event == "FileChanged":
        file_path = _g(payload, "file_path", "filePath")
        detail = f": {file_path}" if file_path else ""
        tmpl = custom or "{prefix} file changed{detail_suffix}"
        return _render(tmpl, **base_kw, file=file_path, detail_suffix=detail)

    if event == "InstructionsLoaded":
        reason = _g(payload, "load_reason", "loadReason", default="session_start")
        tmpl = custom or "{prefix} instructions loaded ({reason})"
        return _render(tmpl, **base_kw, reason=reason)

    if event == "PreCompact":
        trigger = _g(payload, "trigger", "compaction_trigger", default="auto")
        tmpl = custom or "{prefix} context compacting ({trigger})"
        return _render(tmpl, **base_kw, trigger=trigger)

    if event == "PostCompact":
        trigger = _g(payload, "trigger", default="auto")
        tmpl = custom or "{prefix} context compaction finished ({trigger})"
        return _render(tmpl, **base_kw, trigger=trigger)

    if event == "WorktreeCreate":
        wpath = _nested(payload, "hookSpecificOutput", "worktreePath") or _g(
            payload, "worktreePath", "worktree_path"
        )
        detail = f": {wpath}" if wpath else ""
        tmpl = custom or "{prefix} worktree created{detail_suffix}"
        return _render(tmpl, **base_kw, path=wpath, detail_suffix=detail)

    if event == "WorktreeRemove":
        tmpl = custom or "{prefix} worktree removed"
        return _render(tmpl, **base_kw)

    if event == "Elicitation":
        server = _g(payload, "mcp_server_name", "mcpServerName", default="mcp-server")
        action = _g(payload, "action")
        tmpl = custom or "{prefix} MCP elicitation requested ({server})"
        return _render(tmpl, **base_kw, server=server, action=action)

    if event == "ElicitationResult":
        server = _g(payload, "mcp_server_name", "mcpServerName", default="mcp-server")
        action = _g(payload, "action")
        tmpl = custom or "{prefix} MCP elicitation resolved ({server}: {action})"
        return _render(tmpl, **base_kw, server=server, action=action)

    if event == "Notification":
        ntype = _g(payload, "notification_type", "notificationType", "type", default="notice")
        msg = _oneline(_g(payload, "message", "text"), 200)
        detail = f": {msg}" if msg else ""
        tmpl = custom or "{prefix} {notification_type}{detail_suffix}"
        return _render(
            tmpl,
            **base_kw,
            notification_type=ntype,
            message=msg,
            detail_suffix=detail,
        )

    if event == "MessageDisplay":
        msg = _oneline(_g(payload, "content", "message"), 200)
        detail = f": {msg}" if msg else ""
        tmpl = custom or "{prefix} message displayed{detail_suffix}"
        return _render(tmpl, **base_kw, message=msg, detail_suffix=detail)

    if event == "SessionEnd":
        reason = _g(payload, "reason", "end_reason", "endReason", default="unknown")
        tmpl = custom or "{prefix} session ended ({reason})"
        return _render(tmpl, **base_kw, reason=reason)

    tmpl = custom or "{prefix} Claude Code event: {event}"
    return _render(tmpl, **base_kw)

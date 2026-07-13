"""Claude Code provider extension (hooks catalog + usage)."""
from __future__ import annotations

from pathlib import Path

from providers.base import HookEvent, Provider, register
from providers.claude.usage import collect_usage, default_projects_dir

# Full Claude Code event set (mirrors lib/claude-code-events.sh). Formatting
# still lives in adapters/claude-code.sh; this module is the registry source
# of truth for catalog + usage + install defaults.
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


def normalize_event(raw: str) -> str:
    return (raw or "").strip() or "unknown"


PROVIDER = register(
    Provider(
        id="claude",
        display_name="Claude Code",
        config_namespace="claude_code",
        backend_id="claude",
        hook_events=EVENTS,
        normalize_event=normalize_event,
        format_hook=None,  # shell adapter remains authoritative for message text
        usage_source="claude-code",
        usage_default_dir=default_projects_dir(),
        usage_collect=collect_usage,
        model_prefixes=("claude",),
        provider_label="anthropic",
    )
)

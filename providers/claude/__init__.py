"""Claude Code provider extension (hooks catalog + usage)."""

from __future__ import annotations

from providers.base import Provider, register
from providers.claude.hooks import EVENTS, format_hook, normalize_event
from providers.claude.usage import collect_usage, default_projects_dir

PROVIDER = register(
    Provider(
        id="claude",
        display_name="Claude Code",
        config_namespace="claude_code",
        backend_id="claude",
        hook_events=EVENTS,
        normalize_event=normalize_event,
        format_hook=format_hook,
        usage_source="claude-code",
        usage_default_dir=default_projects_dir(),
        usage_collect=collect_usage,
        model_prefixes=("claude",),
        provider_label="anthropic",
    )
)

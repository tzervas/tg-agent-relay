"""Grok Build / Grok CLI provider extension."""
from __future__ import annotations

from providers.base import Provider, register
from providers.grok.hooks import EVENTS, format_hook, normalize_event
from providers.grok.usage import collect_usage, default_sessions_dir

PROVIDER = register(
    Provider(
        id="grok",
        display_name="Grok Build",
        config_namespace="grok",
        backend_id="grok",
        hook_events=EVENTS,
        normalize_event=normalize_event,
        format_hook=format_hook,
        usage_source="grok",
        usage_default_dir=default_sessions_dir(),
        usage_collect=collect_usage,
        model_prefixes=("grok",),
        provider_label="xai",
    )
)

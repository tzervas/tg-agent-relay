"""Generic provider — free-text / label path via relay-notify (no hooks)."""
from __future__ import annotations

from providers.base import Provider, register

PROVIDER = register(
    Provider(
        id="generic",
        display_name="Generic",
        config_namespace="generic",
        backend_id="generic",
        hook_events=[],
        usage_source=None,
        model_prefixes=(),
        provider_label="other",
    )
)

"""Aider (self-hosted coding agent) — delivery via cmd, no hooks."""

from __future__ import annotations

from providers.base import DeliveryPreset, Provider, register

_PRESETS = (
    DeliveryPreset(
        backend_type="aider",
        delivery="cmd",
        model="",
        prefixes=("@aider", "/aider", "aider:"),
        tag="aider",
        cmd=(
            "bash",
            "-lc",
            # Aider message mode; model from env / aider config.
            'cd "${RELAY_CWD:-.}" && aider --message "$RELAY_TEXT" --yes-always 2>&1 '
            "| tail -n 40 "
            '| RELAY_BACKEND=aider "${BRIDGE_DIR:-$HOME/.claude/telegram-bridge}/relay-notify.sh" --raw',
        ),
        notes="Aider coding agent in RELAY_CWD. Install aider-chat separately.",
    ),
)

PROVIDER = register(
    Provider(
        id="aider",
        display_name="Aider",
        config_namespace="aider",
        backend_id="aider",
        hook_events=[],
        usage_source=None,
        model_prefixes=(),
        provider_label="aider",
        backend_types=("aider",),
        delivery_presets=_PRESETS,
        description="Aider self-hosted coding agent (Telegram → aider --message).",
    )
)

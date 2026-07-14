"""Google Gemini provider — delivery backend + model prefixes.

Hook integration for Gemini CLI can be added later via format_hook; today
Telegram inject uses multi-backend ``delivery = "cmd"`` (see presets).
"""

from __future__ import annotations

from providers.base import DeliveryPreset, Provider, register

_PRESETS = (
    DeliveryPreset(
        backend_type="gemini",
        delivery="cmd",
        model="gemini-2.0-flash",
        prefixes=("@gemini", "/gemini", "gemini:"),
        tag="gemini",
        cmd=(
            "bash",
            "-lc",
            # Google AI Studio / Gemini API via curl (set GEMINI_API_KEY).
            'curl -sS "https://generativelanguage.googleapis.com/v1beta/models/'
            '${RELAY_MODEL:-gemini-2.0-flash}:generateContent?key=${GEMINI_API_KEY}" '
            '-H "Content-Type: application/json" '
            '-d "$(jq -n --arg p "$RELAY_TEXT" \'{contents:[{parts:[{text:$p}]}]}\')" '
            "| jq -r '.candidates[0].content.parts[0].text // empty' "
            '| RELAY_BACKEND=gemini "${BRIDGE_DIR:-$HOME/.claude/telegram-bridge}/relay-notify.sh" --raw',
        ),
        notes="Gemini generateContent API. Requires GEMINI_API_KEY.",
    ),
)

PROVIDER = register(
    Provider(
        id="gemini",
        display_name="Google Gemini",
        config_namespace="gemini",
        backend_id="gemini",
        hook_events=[],
        usage_source=None,
        model_prefixes=("gemini", "models/gemini", "gemma"),
        provider_label="google",
        backend_types=("gemini", "google"),
        delivery_presets=_PRESETS,
        description="Google Gemini API delivery backend (plug-and-play cmd preset).",
    )
)

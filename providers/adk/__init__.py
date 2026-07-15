"""Google Agent Development Kit (ADK) — optional delivery + extension bridge.

ADK is **not** a hard dependency. Install separately::

    uv pip install google-adk

Delivery: shell out to ``adk run`` (or your agent module) with ``RELAY_TEXT``.
Tools: ADK agents should attach the relay MCP server so Telegram send/list
go through Bot API allowlist — see docs/ADK_MCP.md.
"""

from __future__ import annotations

from providers.base import DeliveryPreset, Provider, register

_PRESETS = (
    DeliveryPreset(
        backend_type="adk",
        delivery="cmd",
        model="",
        prefixes=("@adk", "/adk", "adk:"),
        tag="adk",
        cmd=(
            "bash",
            "-lc",
            # Prefer project worktree; agent package path is operator-defined.
            'cd "${RELAY_CWD:-.}" && '
            "if command -v adk >/dev/null 2>&1; then "
            '  adk run ${ADK_AGENT:-} --message "$RELAY_TEXT" 2>&1 | tail -n 60; '
            "else "
            '  printf "[adk] google-adk not installed (uv pip install google-adk)\\n%s\\n" "$RELAY_TEXT"; '
            "fi "
            '| RELAY_BACKEND=adk "${BRIDGE_DIR:-$HOME/.claude/telegram-bridge}/relay-notify.sh" --raw',
        ),
        notes=(
            "Optional Google ADK. Set ADK_AGENT / RELAY_CWD. "
            "Attach MCP: python -m tg_agent_relay.mcp_stub --stdio"
        ),
    ),
)

PROVIDER = register(
    Provider(
        id="adk",
        display_name="Google ADK (Agent Development Kit)",
        config_namespace="adk",
        backend_id="adk",
        hook_events=[],
        usage_source=None,
        model_prefixes=(),
        provider_label="google-adk",
        backend_types=("adk", "google-adk"),
        delivery_presets=_PRESETS,
        description=(
            "Optional Google Agent Development Kit delivery. "
            "Pair with relay MCP tools for model-optional extensions."
        ),
    )
)

# Soft-register ADK extension probes when providers load
try:
    from tg_agent_relay.adk_bridge import register_adk_extensions

    register_adk_extensions()
except Exception:
    pass

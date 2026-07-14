"""Relay-native extension bus — tools usable *without* a model in the loop.

Extensions are first-class callables the relay can invoke from:

* MCP ``tools/call`` (agents / IDEs as MCP clients)
* Zero-token Telegram handlers (``/ext …``)
* Optional Google ADK tool wrappers
* Future MCP *client* bridges (relay hosts external MCP servers)

This is **not** a second Telegram stack and does not require an LLM.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Source tags for catalog / policy
SRC_BUILTIN = "builtin"
SRC_PLUGIN = "plugin"
SRC_MCP = "mcp"  # external MCP server tool re-exported
SRC_ADK = "adk"  # Google ADK tool bridge


@dataclass(frozen=True)
class ExtensionTool:
    """One callable tool registered on the relay extension bus."""

    name: str
    description: str
    handler: Callable[[dict[str, Any]], dict[str, Any]]
    input_schema: dict[str, Any] = field(default_factory=dict)
    source: str = SRC_PLUGIN
    # If True, safe for untrusted Telegram keywords (still allowlisted user)
    telegram_safe: bool = True

    def as_mcp_tool(self) -> dict[str, Any]:
        schema = self.input_schema or {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": schema,
        }


_REGISTRY: dict[str, ExtensionTool] = {}


def register_extension(tool: ExtensionTool) -> ExtensionTool:
    """Register or replace an extension tool by name."""
    if not tool.name or not tool.name.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"invalid extension name: {tool.name!r}")
    _REGISTRY[tool.name] = tool
    return tool


def unregister_extension(name: str) -> None:
    _REGISTRY.pop(name, None)


def get_extension(name: str) -> ExtensionTool | None:
    return _REGISTRY.get(name)


def list_extensions(*, source: str | None = None) -> list[ExtensionTool]:
    tools = list(_REGISTRY.values())
    if source:
        tools = [t for t in tools if t.source == source]
    return sorted(tools, key=lambda t: t.name)


def call_extension(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Invoke a registered extension. Always returns a JSON-friendly dict."""
    tool = _REGISTRY.get(name)
    if tool is None:
        return {"ok": False, "error": f"unknown extension: {name!r}"}
    args = dict(arguments or {})
    try:
        result = tool.handler(args)
        if not isinstance(result, dict):
            return {"ok": True, "result": result}
        if "ok" not in result:
            out = {"ok": True, **result}
            return out
        return result
    except Exception as _exc:
        return {
            "ok": False,
            "error": f"{type(_exc).__name__}: {_exc}",
            "extension": name,
        }


def clear_extensions(*, keep_builtin: bool = True) -> None:
    """Test helper — wipe registry (optionally keep builtins)."""
    if not keep_builtin:
        _REGISTRY.clear()
        return
    for name, tool in list(_REGISTRY.items()):
        if tool.source != SRC_BUILTIN:
            del _REGISTRY[name]


# --- Built-in zero-model utilities ----------------------------------------


def _ext_echo(args: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "echo": args.get("text", ""), "note": "relay extension bus (no model)"}


def _ext_list(_args: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "extensions": [
            {
                "name": t.name,
                "description": t.description,
                "source": t.source,
                "telegram_safe": t.telegram_safe,
            }
            for t in list_extensions()
        ],
    }


def _ext_provider_catalog(args: dict[str, Any]) -> dict[str, Any]:
    """List registered platform providers (Grok/Claude/OpenAI/…) — local only."""
    try:
        import providers  # noqa: F401
        from providers.base import list_providers
    except Exception as _exc:
        return {"ok": False, "error": str(_exc)}
    kind = str(args.get("filter") or "all")
    rows = []
    for p in list_providers():
        row = {
            "id": p.id,
            "display_name": p.display_name,
            "capabilities": sorted(p.capabilities),
            "backend_types": list(p.backend_types),
        }
        if kind != "all" and kind not in p.capabilities:
            continue
        rows.append(row)
    return {"ok": True, "count": len(rows), "providers": rows}


def ensure_builtin_extensions() -> None:
    """Idempotent registration of built-in no-model tools."""
    if "relay_ext_echo" not in _REGISTRY:
        register_extension(
            ExtensionTool(
                name="relay_ext_echo",
                description="Echo text via the relay extension bus (no model, no Telegram).",
                handler=_ext_echo,
                input_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                source=SRC_BUILTIN,
            )
        )
    if "relay_ext_list" not in _REGISTRY:
        register_extension(
            ExtensionTool(
                name="relay_ext_list",
                description="List registered relay extensions (no model).",
                handler=_ext_list,
                input_schema={"type": "object", "properties": {}},
                source=SRC_BUILTIN,
            )
        )
    if "relay_provider_catalog" not in _REGISTRY:
        register_extension(
            ExtensionTool(
                name="relay_provider_catalog",
                description="List plug-and-play providers (hooks/delivery/usage) from the registry.",
                handler=_ext_provider_catalog,
                input_schema={
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "description": "all | hooks | usage | delivery | openai_compat",
                            "default": "all",
                        }
                    },
                },
                source=SRC_BUILTIN,
            )
        )


ensure_builtin_extensions()


def mcp_tool_definitions() -> list[dict[str, Any]]:
    """MCP tools/list entries for all extensions (plus meta tools)."""
    ensure_builtin_extensions()
    tools = [t.as_mcp_tool() for t in list_extensions()]
    # Meta: call any extension by name (for clients that prefer one entrypoint)
    tools.append(
        {
            "name": "relay_call_extension",
            "description": (
                "Call a relay-native extension by name (no model required). "
                "Use relay_ext_list to discover names."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Extension tool name"},
                    "arguments": {
                        "type": "object",
                        "description": "Arguments for the extension",
                        "additionalProperties": True,
                    },
                },
                "required": ["name"],
            },
        }
    )
    return tools


def dispatch_mcp_extension_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Handle tools/call for extension names or relay_call_extension."""
    args = dict(arguments or {})
    if name == "relay_call_extension":
        ext = str(args.get("name") or "")
        inner = args.get("arguments") if isinstance(args.get("arguments"), dict) else {}
        payload = call_extension(ext, inner)
        return {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
            "isError": not payload.get("ok", False),
        }
    if name in _REGISTRY:
        payload = call_extension(name, args)
        return {
            "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
            "isError": not payload.get("ok", False),
        }
    return {
        "content": [{"type": "text", "text": f"unknown extension tool: {name!r}"}],
        "isError": True,
    }

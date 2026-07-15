#!/usr/bin/env python3
"""Offline tests: extension bus + optional ADK bridge + MCP merge."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tg_agent_relay.adk_bridge import (
    adk_mcp_config_snippet,
    probe_adk,
    register_adk_extensions,
)
from tg_agent_relay.extensions import (
    SRC_PLUGIN,
    ExtensionTool,
    call_extension,
    clear_extensions,
    ensure_builtin_extensions,
    list_extensions,
    register_extension,
)
from tg_agent_relay.mcp_stub import McpFacade, list_tools

PASS = 0
FAIL = 0


def ok(name: str) -> None:
    global PASS
    PASS += 1
    print(f"PASS  {name}")


def fail(name: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"FAIL  {name}")
    if detail:
        print(f"      {detail}")


def true(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail or "false")


def eq(name: str, a, b) -> None:
    if a == b:
        ok(name)
    else:
        fail(name, f"{a!r} != {b!r}")


def main() -> int:
    clear_extensions(keep_builtin=False)
    ensure_builtin_extensions()
    names = {t.name for t in list_extensions()}
    true("builtin echo", "relay_ext_echo" in names)
    true("builtin list", "relay_ext_list" in names)
    true("builtin catalog", "relay_provider_catalog" in names)

    r = call_extension("relay_ext_echo", {"text": "hi"})
    true("echo ok", r.get("ok") is True)
    eq("echo text", "hi", r.get("echo"))

    r2 = call_extension("no_such_tool", {})
    true("unknown not ok", r2.get("ok") is False)

    def _double(args):
        return {"ok": True, "n": int(args.get("n", 0)) * 2}

    register_extension(
        ExtensionTool(
            name="relay_test_double",
            description="test",
            handler=_double,
            input_schema={
                "type": "object",
                "properties": {"n": {"type": "integer"}},
            },
            source=SRC_PLUGIN,
        )
    )
    eq("plugin double", 10, call_extension("relay_test_double", {"n": 5}).get("n"))

    # ADK probe never requires install
    p = probe_adk()
    true("probe returns AdkProbe-like", hasattr(p, "available"))
    d = p.as_dict()
    true("probe dict has install_hint", "install_hint" in d)

    register_adk_extensions()
    adk = call_extension("relay_adk_probe", {})
    true("adk probe extension ok", adk.get("ok") is True)
    cfg = call_extension("relay_adk_mcp_config", {"python": "python3"})
    true("adk mcp config ok", cfg.get("ok") is True)
    true("mcpServers in config", "mcpServers" in (cfg.get("config") or {}))

    snippet = adk_mcp_config_snippet()
    true("snippet has tg-agent-relay", "tg-agent-relay" in snippet.get("mcpServers", {}))

    # MCP list includes extensions
    tools = list_tools()
    tnames = {t["name"] for t in tools}
    true("mcp has relay_send", "relay_send" in tnames)
    true("mcp has relay_call_extension", "relay_call_extension" in tnames)
    true("mcp has relay_ext_list", "relay_ext_list" in tnames)
    true("mcp has relay_adk_probe", "relay_adk_probe" in tnames)

    fac = McpFacade(dry_run=True)
    out = fac.call_tool("relay_ext_echo", {"text": "mcp"})
    true("facade extension not error", out.get("isError") is not True)
    body = out.get("content", [{}])[0].get("text", "")
    true("facade echo body", "mcp" in body)

    out2 = fac.call_tool(
        "relay_call_extension",
        {"name": "relay_test_double", "arguments": {"n": 3}},
    )
    payload = json.loads(out2["content"][0]["text"])
    eq("call_extension via mcp", 6, payload.get("n"))

    # provider adk registered
    import providers  # noqa: F401
    from providers.base import get_provider

    true("adk provider registered", get_provider("adk") is not None)

    print()
    print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

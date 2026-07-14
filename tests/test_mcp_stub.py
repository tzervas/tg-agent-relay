#!/usr/bin/env python3
"""tests/test_mcp_stub.py — MCP facade stub schemas + dry-run dispatch (#37).

NO network calls. Verifies tools/list schemas and local tools/call handlers
for relay_send (dry_run), relay_list_projects, and JSON-RPC wiring.

Run:
  python3 tests/test_mcp_stub.py
  uv run python tests/test_mcp_stub.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tg_agent_relay.mcp_stub import (
    TOOL_DEFINITIONS,
    TOOL_NAMES,
    McpFacade,
    list_tools,
    main,
    tools_list_result,
)

PASS = FAIL = 0


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


def eq(name: str, exp, act) -> None:
    if exp == act:
        ok(name)
    else:
        fail(name, f"expected {exp!r} got {act!r}")


def true(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail)


# --- schemas / tools/list ---------------------------------------------------
tools = list_tools()
names = {t["name"] for t in tools}
# Core Bot API tools always present
for core in ("relay_send", "relay_list_projects", "relay_usage_summary"):
    true(f"list_tools includes {core}", core in names)
# Extension bus (no model) tools
true("list_tools includes relay_call_extension", "relay_call_extension" in names)
true("list_tools includes relay_ext_list", "relay_ext_list" in names)
true("TOOL_NAMES is core-only subset", names >= TOOL_NAMES)
true("list_tools has more than core 3", len(tools) >= 3)

for t in tools:
    n = t["name"]
    true(f"{n} has description", bool(t.get("description")))
    schema = t.get("inputSchema") or {}
    eq(f"{n} inputSchema type object", "object", schema.get("type"))
    true(f"{n} has properties", isinstance(schema.get("properties"), dict))

send_schema = next(t for t in TOOL_DEFINITIONS if t["name"] == "relay_send")
eq(
    "relay_send requires text",
    ["text"],
    send_schema["inputSchema"].get("required"),
)
true(
    "relay_send properties include dry_run",
    "dry_run" in send_schema["inputSchema"]["properties"],
)

bundle = tools_list_result()
true("tools_list_result has tools key", "tools" in bundle)
true("tools_list_result length >= 3", len(bundle["tools"]) >= 3)

# --- McpFacade dry_run send (no network) ------------------------------------
cfg = {
    "projects": {
        "mycelium": {"root": "/tmp/mycelium-proj", "default_backend": "claude"},
        "other": {"root": "/tmp/other"},
    },
    "backends": {
        "claude": {"prefixes": ["@claude"]},
        "grok": {"prefixes": ["@grok"]},
    },
    "chats": [
        {"chat_id": -1001, "project": "mycelium", "backend": "claude"},
        {"chat_id": -1002, "project": "orphan-only"},
    ],
}
facade = McpFacade(cfg=cfg, dry_run=True)

# Patch EnvSender / send_message to fail the test if network path is taken.
with mock.patch("tg_agent_relay.send.send_message") as sm:
    sm.side_effect = AssertionError("network send_message must not be called in dry_run")
    result = facade.call_tool(
        "relay_send",
        {"text": "hello from mcp stub", "backend": "claude", "project": "mycelium"},
    )
    true("dry_run send isError false", result.get("isError") is False)
    body = result["content"][0]["text"]
    plan = json.loads(body)
    eq("dry_run status", "dry_run", plan.get("status"))
    eq("dry_run dry_run flag", True, plan.get("dry_run"))
    true("dry_run text preserved", "hello from mcp stub" in plan.get("text", ""))
    eq("send_message call count", 0, sm.call_count)

# Missing text → error
err = facade.call_tool("relay_send", {})
true("missing text isError", err.get("isError") is True)

# Unknown tool
unk = facade.call_tool("relay_not_a_tool", {})
true("unknown tool isError", unk.get("isError") is True)

# --- relay_list_projects ----------------------------------------------------
lp = facade.call_tool("relay_list_projects", {})
true("list_projects isError false", lp.get("isError") is False)
lp_body = json.loads(lp["content"][0]["text"])
eq("list_projects count", 3, lp_body.get("count"))
slugs = {p["slug"] for p in lp_body.get("projects", [])}
true("list_projects has mycelium", "mycelium" in slugs)
true("list_projects has other", "other" in slugs)
true("list_projects has orphan chat binding", "orphan-only" in slugs)

# --- JSON-RPC tools/list ----------------------------------------------------
rpc = facade.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
true("jsonrpc tools/list has result", rpc is not None and "result" in rpc)
eq("jsonrpc tools/list id", 1, rpc.get("id"))
rpc_tools = rpc["result"]["tools"]
true("jsonrpc tools/list count >= 3", len(rpc_tools) >= 3)

# initialize
init = facade.handle_jsonrpc(
    {
        "jsonrpc": "2.0",
        "id": 0,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {}},
    }
)
true("initialize has serverInfo", "serverInfo" in (init or {}).get("result", {}))
eq(
    "initialize server name",
    "tg-agent-relay",
    init["result"]["serverInfo"]["name"],
)

# tools/call via JSON-RPC
rpc_call = facade.handle_jsonrpc(
    {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "relay_list_projects",
            "arguments": {},
        },
    }
)
true("jsonrpc tools/call has content", "content" in rpc_call["result"])

# notification → None
note = facade.handle_jsonrpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
eq("notification returns None", None, note)

# unknown method
bad = facade.handle_jsonrpc({"jsonrpc": "2.0", "id": 9, "method": "nope/nope"})
true("unknown method has error", "error" in bad)

# --- main dumps tools without network ---------------------------------------
import io
from contextlib import redirect_stdout

buf = io.StringIO()
with redirect_stdout(buf):
    rc = main([])
eq("main() exit 0", 0, rc)
dumped = json.loads(buf.getvalue())
true("main() dumps tools >= 3", len(dumped.get("tools", [])) >= 3)

print()
print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)

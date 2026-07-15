# Google ADK + MCP extensions (optional)

This relay is **Bot API + allowlist first**. Optional layers:

1. **MCP server** — agents/IDEs call relay tools (`relay_send`, extensions, …)
2. **Extension bus** — tools that run **without a model** (handlers, MCP, ADK)
3. **Google ADK** — soft dependency (`google-adk`); delivery + MCP attach

None of these are required to run Grok/Claude/OpenAI delivery.

---

## Why this exists

| Need | Approach |
|---|---|
| IDE/agent calls Telegram through **our** security model | MCP server over `EnvSender` / poll allowlist |
| Tools that are **not** “ask an LLM” | Extension bus (`tg_agent_relay.extensions`) |
| Google Agent Development Kit agents | Optional ADK delivery + MCP tool attach |
| Self-hosted / other MCP servers as tools | Register as extensions (stdio client later) |

**Do not** replace the relay with Telethon userbot MCP.

---

## MCP server (relay as tool host)

```bash
# List tools (includes extension bus)
python -m tg_agent_relay.mcp_stub

# Stdio JSON-RPC (for ADK / Cursor / Claude desktop MCP config)
python -m tg_agent_relay.mcp_stub --stdio
```

### Core tools (Bot API)

| Tool | Model required? | Notes |
|---|---|---|
| `relay_send` | No | Default `dry_run=true` |
| `relay_list_projects` | No | Config only |
| `relay_usage_summary` | No | Local transcripts |

### Extension-bus tools (no model)

| Tool | Purpose |
|---|---|
| `relay_ext_list` | List extensions |
| `relay_ext_echo` | Echo (smoke test) |
| `relay_provider_catalog` | List Grok/Claude/OpenAI/… providers |
| `relay_adk_probe` | Detect optional `google-adk` |
| `relay_adk_mcp_config` | Emit MCP config snippet for ADK clients |
| `relay_call_extension` | Generic dispatcher by name |

Register your own:

```python
from tg_agent_relay.extensions import ExtensionTool, register_extension

def my_tool(args: dict) -> dict:
    return {"ok": True, "got": args}

register_extension(ExtensionTool(
    name="relay_my_tool",
    description="Does a thing without an LLM",
    handler=my_tool,
    input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
))
```

---

## Zero-token Telegram: `/ext`

```toml
[commands.ext]
keyword = "ext"
slash = "/ext"
mode = "relay"
handler = "handlers/ext.sh"
```

```
/ext list
/ext echo hello
/ext providers
/ext adk
/ext adk_mcp
```

---

## Google ADK (Python, optional)

```bash
# Not required by the relay:
uv pip install "google-adk"
# or: uv sync --extra adk   # if using project optional-deps
```

### Probe

```bash
python -c "from tg_agent_relay.adk_bridge import probe_adk; print(probe_adk())"
# or Telegram: /ext adk
```

### Delivery backend (Telegram → ADK agent)

```toml
[backends.adk]
type = "adk"
delivery = "cmd"
prefixes = ["@adk", "adk:"]
tag = "adk"
# See: python lib/provider_catalog.py presets adk
```

Or use catalog presets from `providers/adk`.

### ADK uses the relay as MCP tools (recommended)

1. Run / configure MCP server: `python -m tg_agent_relay.mcp_stub --stdio`
2. In ADK (or any MCP client), add server config from:

```bash
python -c "from tg_agent_relay.adk_bridge import adk_mcp_config_snippet; import json; print(json.dumps(adk_mcp_config_snippet(), indent=2))"
```

3. ADK tools then call `relay_send` / extensions — **still Bot token + allowlist**, not a parallel Telegram client.

Keep `RELAY_MCP_DRY_RUN=true` (or facade `dry_run`) until you trust the host.

### Rust

Google’s public ADK is **Python** (and Java/web). This repo’s **MSRV 1.96** Rust workspace is ready for optional crates (`crates/…`) if you later want:

* high-volume MCP JSON-RPC
* format/pagination hotspots

There is no hard dependency on a Google “ADK Rust” package today. Prefer Python ADK + MCP attach; add Rust only after profiling (#22 / #41).

---

## External MCP servers as extensions (pattern)

Goal: use **filesystem / git / browser** MCP servers from Telegram **without** sending the user message to a cloud model first.

**Today (landed):**

* In-process extension registry + MCP re-export of those tools
* Manual registration of Python handlers

**Next (optional):**

```toml
# Future sketch — not fully implemented as a process manager yet
[[extensions.mcp_servers]]
name = "filesystem"
command = ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/safe/path"]
```

Until the stdio client manager lands, wrap external tools in a small Python
`ExtensionTool` that shells out, or run them only inside ADK/Cursor with the
relay MCP for Telegram I/O.

---

## Security

| Invariant | How |
|---|---|
| Bot token only | Live send via `EnvSender` |
| Inbound allowlist | `tg-poll` only |
| Extensions default local | No network unless tool implements it |
| ADK optional | Soft import; missing package is fine |
| MCP dry-run default | No accidental public sends |

Treat enabling live MCP `relay_send` like granting shell access to the bridge host.

---

## Related

* [PROVIDERS.md](PROVIDERS.md) — OpenAI / Ollama / plug-and-play backends  
* [TELEGRAM_MCP.md](TELEGRAM_MCP.md) — Telegram MCP assessment  
* [ROUTING.md](ROUTING.md) — multi-backend rooms  
* `tg_agent_relay/extensions.py`, `adk_bridge.py`, `mcp_stub.py`  
* `providers/adk/`, `handlers/ext.sh`

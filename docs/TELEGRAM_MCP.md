# Telegram MCP assessment + optional facade stub

## Summary recommendation

**Keep the custom Bot API client as the product core** (this repo’s
`tg-send` / `tg-poll` / allowlist / TTS / routing). Optionally add a thin
**MCP facade over that API** so agents can call relay tools without
reimplementing Telegram.

Do **not** replace the relay with a community Telethon userbot MCP by default.

## Status (issue #37)

| Item | State |
|---|---|
| Design decision (Bot API core, MCP optional) | **Done** (this doc) |
| Tool specs: `relay_send`, `relay_list_projects`, `relay_usage_summary` | **Done** |
| Minimal in-process stub (`tg_agent_relay/mcp_stub.py`) | **Done** (schemas + dry-run dispatch) |
| Production MCP server / packaging / deploy | **Out of scope** |
| Telethon / user-session MCP as default | **Rejected** |

### Stub module

```text
tg_agent_relay/mcp_stub.py
```

- **Not** a second Telegram client. Handlers call package modules:
  - `relay_send` → `routing.lookup_chat` + `send.EnvSender` (live only when `dry_run=false`)
  - `relay_list_projects` → `config.load_config` / `[projects]` + chat bindings
  - `relay_usage_summary` → `lib/usage_ingest.collect` (local transcripts only)
- **Default `dry_run=True`** — `tools/list` and local `tools/call` never hit
  `api.telegram.org` unless the operator opts in.
- MCP-shaped JSON-RPC methods: `initialize`, `ping`, `tools/list`, `tools/call`
  (stdio line protocol via `python -m tg_agent_relay.mcp_stub --stdio`).
- No MCP SDK dependency; no production transport hardening.

```bash
# Offline: print tool schemas (tools/list)
python -m tg_agent_relay.mcp_stub

# Optional: one JSON-RPC object per stdin line
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | python -m tg_agent_relay.mcp_stub --stdio
```

### Tool catalog

| Tool | Inputs (required bold) | Behavior |
|---|---|---|
| `relay_send` | **`text`**, `backend?`, `project?`, `chat_id?`, `thread_id?`, `parse_mode?`, `dry_run?` | Plan or Bot API send via our `EnvSender` |
| `relay_list_projects` | `config_path?` | List project slugs/roots from config (local) |
| `relay_usage_summary` | `source?`, `projects_dir?`, `window?` | Local usage aggregate (`usage_ingest`) |

`relay_bind_project` remains **deferred** (careful mutation of chat overlays;
not in the #37 stub surface).

### Security (unchanged allowlist model)

| Invariant | How the stub preserves it |
|---|---|
| Bot token only (no phone/session files) | Live send uses `BOT_TOKEN` from `.env` / env; never Telethon string sessions |
| Inbound allowlist (`ALLOWED_USER_ID`) | Still enforced by `tg-poll` / poll port — MCP does **not** accept arbitrary Telegram inbound |
| Outbound chat target | `ALLOWED_CHAT_ID` or route `lookup_chat`; no open “message anyone” surface |
| No new secret types | No OAuth, no user MTProto, no extra API keys for the facade itself |
| Network opt-in | `dry_run` defaults **true**; missing `BOT_TOKEN` refuses live send |

Threat note: an MCP **client** that can call `relay_send` with `dry_run=false`
can outbound-message whatever chat the bot token can reach (same as any local
process that can run `tg-send`). Treat local MCP enablement like granting
shell access to the bridge host — keep the bot token file mode `0600`, and
do not expose the stdio server on a public network without an auth layer
(full deploy remains out of scope).

## What exists (2026)

There is **no official Telegram-company MCP server**. Community options include:

| Style | Example stack | Auth model |
|---|---|---|
| Bot API MCP | Telegraf / Bot token | Bot identity; limited admin capabilities |
| User MTProto MCP | Telethon / pyrogram | User session (phone login / string session) |

Sources are third-party (e.g. mcp-telegram Bot wrappers, Telethon “control my Telegram account” servers). Capabilities and threat models differ sharply.

## Fit vs this relay’s security model

| Requirement | Custom Bot API relay | Bot MCP (community) | Userbot MCP (Telethon) | **Our MCP stub** |
|---|---|---|---|---|
| Strict `ALLOWED_USER_ID` allowlist | ✅ first-class | ⚠️ reimplement | ⚠️ different trust boundary | ✅ poll path unchanged |
| Zero-token relay handlers | ✅ | usually missing | N/A | N/A (tools ≠ handlers) |
| Project rooms + overlay binds | ✅ | missing | possible but different IDs | ✅ list via config; bind deferred |
| Local TTS + format + metrics | ✅ | missing | missing | via same package later |
| No user phone session secrets | ✅ bot token only | ✅ | ❌ session files | ✅ bot token only |
| Forum topics | ✅ via `message_thread_id` | varies | strong | ✅ `thread_id` on `relay_send` |

Userbot MCPs can manage topics and history well, but they introduce **user-session secrets**, broader blast radius, and a ToS/risk profile this project deliberately avoided (outbound bot + inbound allowlist).

## Adoption options

1. **Status quo (recommended core)** — pure Bot API via our scripts / Python package.
2. **MCP wrapper (recommended extension)** — **stub landed** (`mcp_stub.py`); expose tools:
   - `relay_send(text, backend?, project?)`
   - `relay_list_projects`
   - `relay_usage_summary`
   implementing them by calling the same Python modules as the CLI — **not** a second Telegram stack.
3. **Embed third-party Bot MCP** — only if it can sit *behind* our allowlist and command router; otherwise reject.

## Migration note (shell → Python)

As the core moves to Python (`tg_agent_relay` package, progressive strangler), the MCP facade stays a thin entrypoint over the same package. A future production MCP server would wrap `McpFacade` with a real transport (stdio/SSE) and optional auth — still no Telethon default. Rust remains optional for hotspots only after profiling.

## Decision

- **Ship:** native Bot API path (current + Python port).
- **Document:** this file.
- **Stub (opt-in, #37):** `tg_agent_relay.mcp_stub` — tool schemas + dry-run dispatch over our modules.
- **Extensions + ADK:** see [ADK_MCP.md](ADK_MCP.md) — extension bus (no model), Google ADK optional, MCP tool host for agents/IDEs.
- **Later:** production MCP packaging if agent clients need it; evaluate community Bot MCP only as a dependency of that facade if it reduces code — never Telethon as the default security path.

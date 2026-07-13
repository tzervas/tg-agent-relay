# Telegram MCP assessment

## Summary recommendation

**Keep the custom Bot API client as the product core** (this repo’s
`tg-send` / `tg-poll` / allowlist / TTS / routing). Optionally add a thin
**MCP facade over that API** later so agents can call relay tools without
reimplementing Telegram.

Do **not** replace the relay with a community Telethon userbot MCP by default.

## What exists (2026)

There is **no official Telegram-company MCP server**. Community options include:

| Style | Example stack | Auth model |
|---|---|---|
| Bot API MCP | Telegraf / Bot token | Bot identity; limited admin capabilities |
| User MTProto MCP | Telethon / pyrogram | User session (phone login / string session) |

Sources are third-party (e.g. mcp-telegram Bot wrappers, Telethon “control my Telegram account” servers). Capabilities and threat models differ sharply.

## Fit vs this relay’s security model

| Requirement | Custom Bot API relay | Bot MCP (community) | Userbot MCP (Telethon) |
|---|---|---|---|
| Strict `ALLOWED_USER_ID` allowlist | ✅ first-class | ⚠️ reimplement | ⚠️ different trust boundary |
| Zero-token relay handlers | ✅ | usually missing | N/A |
| Project rooms + overlay binds | ✅ | missing | possible but different IDs |
| Local TTS + format + metrics | ✅ | missing | missing |
| No user phone session secrets | ✅ bot token only | ✅ | ❌ session files |
| Forum topics | ✅ via `message_thread_id` | varies | strong |

Userbot MCPs can manage topics and history well, but they introduce **user-session secrets**, broader blast radius, and a ToS/risk profile this project deliberately avoided (outbound bot + inbound allowlist).

## Adoption options

1. **Status quo (recommended core)** — pure Bot API via our scripts / future Python package.
2. **MCP wrapper (recommended extension)** — expose tools like:
   - `relay_send(text, backend?, project?)`
   - `relay_list_projects`
   - `relay_bind_project` (careful)
   - `relay_usage_summary`
   implementing them by calling the same Python modules as the CLI — **not** a second Telegram stack.
3. **Embed third-party Bot MCP** — only if it can sit *behind* our allowlist and command router; otherwise reject.

## Migration note (shell → Python)

As the core moves to Python (`tg_agent_relay` package, progressive strangler), an MCP server becomes a thin `mcp` entrypoint over the same package. Rust remains optional for hotspots only after profiling.

## Decision

- **Ship:** native Bot API path (current + Python port).
- **Document:** this file.
- **Later (opt-in):** MCP facade; evaluate community Bot MCP only as a dependency of that facade if it reduces code — never Telethon as the default security path.

# Forum threads (session / repo / workstream)

Telegram forum topics are **flat** (one `message_thread_id` level). TG Agent Relay
encodes orchestrator hierarchy in **topic titles** and **overlay bindings**, not
nested forums.

See also: [ROUTING.md](ROUTING.md) (project rooms, `[[chats]]`, `/project bind`).

## Concepts

| Field | Meaning |
|-------|---------|
| `session` | Long-running orchestrator / L0 session id |
| `project` / `repo` | Repository slug (same as routing `project`) |
| `workstream` | Named work line inside session+repo |
| `platform` | Harness chat bucket: `grok`, `claude`, `codex`, `self_hosted`, … |
| `handle` | Optional `@handle` metadata on the binding row |

### Topic title patterns

```text
{session_short}
{session_short} · {repo_short}
{session_short} · {repo_short} · {workstream}
```

`session_short` is the full id when ≤8 characters, otherwise the first four characters.

## Configuration

```toml
[threads]
enabled = true
auto_create = true
max_creates_per_hour = 30

[threads.platform_chats]
grok = -1001111111111
claude = -1002222222222

[commands.thread]
keyword = "thread"
slash = "/thread"
mode = "relay"
handler = "handlers/thread.sh"
```

Bindings are stored in gitignored `.chats.d/bindings.json` (merged over static
`[[chats]]`), same overlay as `/project bind`. Extended rows may include
`session`, `workstream`, `platform`, and `handle`.

## Commands

| Command | Action |
|---------|--------|
| `/thread list` | List overlay/static rows that carry a `session` |
| `/thread here` | Show `chat_id`, `thread_id`, and resolve preview |
| `/thread bind session=<id> [repo=<slug>] [ws=<slug>] …` | Bind **this** topic without creating |
| `/thread ensure session=<id> [repo=<slug>] [ws=<slug>] [platform=grok]` | Lookup or `createForumTopic` + bind |

Only `ALLOWED_USER_ID` may use commands (same as all inbound). Topic **create** is
allowed only in chats listed under `[threads.platform_chats]`, optional
`threads.allowed_chat_ids`, or `ALLOWED_CHAT_ID` when it is a forum.

## Outbound routing

Export before `relay-notify.sh` / hooks / adapters:

```bash
export RELAY_SESSION=019f6d8a-…
export RELAY_PROJECT=tg-agent-relay   # or RELAY_REPO
export RELAY_WORKSTREAM=p13-threads
export RELAY_PLATFORM=grok
export RELAY_AGENT_HANDLE=tgagentrelay-p13
```

Resolution order (implemented in `tg_agent_relay/threads.py`):

1. Explicit `RELAY_CHAT_ID` + `RELAY_THREAD_ID`
2. Binding: session + project + workstream (then looser tiers)
3. Project room (`lookup_project` / existing `[[chats]]`)
4. Platform default chat (`[threads.platform_chats]`, no thread)
5. `ALLOWED_CHAT_ID` when session/platform/workstream/handle hints are set

Optional first-line stamp: `🧵 {title}` (disable with `RELAY_THREAD_STAMP=0`).

## Operator checklist (one-time)

1. Create supergroups per platform (e.g. **Agents · Grok**), enable **Topics**, add bot as admin (**Manage topics**).
2. Copy each supergroup `chat_id` into `[threads.platform_chats]`.
3. Set `[threads] enabled = true` and `auto_create = true` (or bind manually).
4. Enable `[commands.thread]` in `relay.toml`.
5. From orchestrator start: `export RELAY_SESSION=… RELAY_PLATFORM=grok` or `/thread ensure session=… repo=…`.
6. Restart inbound: `bash scripts/ensure-inbound.sh`.

## Security

- Rate limit: `max_creates_per_hour` (default 30) persisted under `.chats.d/.threads.d/`.
- Bot token never logged; topic titles must not contain secrets.
- Complements `@handle` routing; does not replace it.
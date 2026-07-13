# Multi-backend routing

One Telegram bot, many agent backends (Claude Code, Grok Build, Ollama,
llama.cpp, …), with **isolated chats** and/or **prefix routing** in a
unified chat. Project bindings keep concurrent backends on the same repo
from intermingling chat history.

**Backward compatible:** with no `[backends]` / `[[chats]]` in
`relay.toml`, inbound/outbound behavior is unchanged (single
`ALLOWED_CHAT_ID`, plain `[telegram] …` lines).

## Concepts

| Concept | Meaning |
|---|---|
| **Backend** | Named provider instance (`[backends.<id>]`) with delivery mode, prefixes, optional default project |
| **Chat binding** | A Telegram `chat_id` (+ optional forum `thread_id`) sticky-routed to one backend + project |
| **Unified chat** | Your DM (`ALLOWED_CHAT_ID`) without a sticky binding — route via message prefixes |
| **Project** | Repo slug or path; optional per-backend worktrees under `[projects.<id>]` |

## Security

- Only `ALLOWED_USER_ID` may send (even in groups — other members are ignored).
- Accepted chats: `ALLOWED_CHAT_ID` and any `[[chats]].chat_id`. Unlisted groups are dropped.
- Route table lives in `relay.toml` (no secrets). `.env` still holds only the bot token + allowlist ids.

## Quick setup

### 1. Define backends

```toml
[routing]
default_backend = "claude"
require_prefix = false
tag_style = "bracket"   # "[claude · mycelium] …"

[backends.claude]
type = "claude-code"
delivery = "stdout"       # Monitor reads tg-poll.sh stdout
tag = "claude"
prefixes = ["@claude", "/claude", "claude:"]
project = "mycelium"

[backends.grok]
type = "grok"
delivery = "fifo"
fifo = "~/.grok/telegram-bridge/in.fifo"
tag = "grok"
prefixes = ["@grok", "/grok", "grok:"]
project = "mycelium"

[backends.ollama]
type = "ollama"
delivery = "cmd"
model = "llama3.2"
# cmd is a JSON array or string; env provides RELAY_TEXT, RELAY_PROJECT, …
cmd = ["bash", "-lc", "ollama run \"$RELAY_MODEL\" \"$RELAY_TEXT\""]
tag = "ollama"
prefixes = ["@ollama", "/ollama", "ollama:"]
project = "mycelium"
```

### 2. Project-split chats (recommended)

Isolate by **repository/project**, not only by backend. Switch projects by
switching Telegram rooms.

**Pattern A — forum topics (one group, many topics):**

1. Create a supergroup, enable Topics, add the bot.
2. Create one topic per repo.
3. In each topic: `/project bind mycelium` (records chat_id + thread_id).

**Pattern B — separate group per project:**

1. Create a group per repo, add the bot.
2. In that group: `/project bind mycelium`.

Static config (optional — bind command prefers overlay):

```toml
# Project-only room: sticky project; backend via @prefix or default
[[chats]]
chat_id = -100999
thread_id = 3
project = "mycelium"
# backend unset on purpose

# Or fully sticky backend + project
[[chats]]
chat_id = -100888
project = "mycelium"
backend = "claude"
```

Overlay binds live in gitignored `.chats.d/bindings.json` and merge over
`[[chats]]` at load time.

Hook pings use `project_from_cwd` so status from a repo lands in that
project’s room when bound.

### 3. Unified chat prefixes

In your normal DM with the bot (not listed as a sticky `[[chats]]` row, or
only as default):

```
@claude implement the parser
@grok review the parser
@ollama summarize the README
```

Unprefixed messages use `[routing].default_backend`. Set
`require_prefix = true` to reject unprefixed traffic with a short help
nudge instead.

### 4. Project worktrees (collision avoidance)

Multiple backends on one repo should not share one dirty working tree:

```toml
[projects.mycelium]
root = "~/work/mycelium"
allow_shared_worktree = false

[projects.mycelium.worktrees]
claude = "~/work/mycelium"
grok = "~/work/mycelium-wt-grok"
ollama = "~/work/mycelium-wt-ollama"
```

Create worktrees yourself (`git worktree add …`). The relay sets
`RELAY_CWD` for `cmd` delivery; it does not auto-create worktrees.

## Delivery modes

| Mode | Use when |
|---|---|
| `stdout` | Single backend consuming `tg-poll.sh` as a Monitor (legacy Claude). For multi-stdout, run pollers with `TG_POLL_BACKEND=<id>` so each process only emits its backend’s lines — or prefer `fifo`. |
| `fifo` | Dedicated reader per backend (`adapters/backend-fifo-reader.sh <fifo>`). |
| `cmd` | Fire-and-forget inject (Ollama CLI, curl to llama.cpp, tmux send-keys, …). Never blocks the poll loop. |

Inbound event lines look like:

```text
[telegram:backend:claude:project:mycelium] implement the parser
```

Outbound replies are tagged:

```text
[claude · mycelium]
✅ done — …
```

Adapters set `RELAY_BACKEND` automatically (`claude` / `grok`) so hook
pings return to the bound chat when `[[chats]]` reverse-lookup matches.

## Grok hooks

```bash
# Enable lifecycle pings in relay.toml [grok.*], then:
bash install-grok-hooks.sh --dry-run
bash install-grok-hooks.sh
```

Writes `~/.grok/hooks/tg-agent-relay.json` only (merge-safe with other Grok hooks).

## Identifying who replied

| Signal | How |
|---|---|
| Switch chat/topic | Sticky `[[chats]]` — history is already isolated |
| Prefix in unified chat | `@grok …` / `@claude …` |
| Reply tag | `[backend · project]` on every routed outbound message |
| Inbound Monitor tag | `[telegram:backend:…:project:…]` |

## Commands

Relay-handled commands (`/dashboard`, `/usage`, …) still cost zero model
tokens and reply to the **originating** chat (`RELAY_CHAT_ID` from the
poller), not only the legacy `ALLOWED_CHAT_ID`.

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

For **orchestrator sessions** and optional per-platform supergroups, use
[THREADS.md](THREADS.md): flat topic titles encode `session · repo · workstream`,
`/thread ensure`, and outbound `RELAY_SESSION` / `RELAY_PLATFORM` routing.

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

## Multi-session Grok handles

When several **Grok Build** sessions run on one machine, give each a unique
**@handle** and FIFO so Telegram traffic is split automatically (no
ignore-vs-handle logic in the agent).

**Option A — static `relay.toml` (N backends):**

```toml
[backends.cabal]
type = "grok"
delivery = "fifo"
fifo = "~/.grok/telegram-bridge/sessions/cabal.fifo"
tag = "cabal"
prefixes = ["@cabal", "/cabal", "cabal:"]

[backends.fleet]
type = "grok"
delivery = "fifo"
fifo = "~/.grok/telegram-bridge/sessions/fleet.fifo"
tag = "fleet"
prefixes = ["@fleet", "/fleet", "fleet:"]
```

Longest-prefix routing ensures `@cabal2` does not steal `@cabal` traffic.

**Option B — dynamic registry (recommended for local sessions):**

```toml
[sessions]
# dir = "~/.claude/telegram-bridge/.sessions.d"
```

```bash
bash scripts/register-session.sh --handle cabal
bash scripts/register-session.sh --handle fleet
# Monitor per FIFO: adapters/backend-fifo-reader.sh <fifo>
```

Session JSON rows **overlay** static `[backends.<same-id>]` (session wins).
Full operator guide: [SESSIONS.md](SESSIONS.md).

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

## End-to-end checklist: project rooms

Use this offline-friendly checklist when wiring **project rooms** (forum
topics or whole groups). Auto-creating Telegram topics is **out of scope**
— create rooms/topics yourself, then bind.

### Prerequisites

- [ ] Bot in the target supergroup (or group); you are `ALLOWED_USER_ID`.
- [ ] `[projects.<slug>]` defined in `relay.toml` with `root = "…"` (for
      hook `project_from_cwd` reverse-lookup).
- [ ] At least one `[backends.*]` (or sticky `backend` on the bind).
- [ ] Relay-handled `/project` enabled (copy `[commands.project]` from
      `relay.toml.example`).
- [ ] `jq` available on the host (overlay writes need it).

### Pattern A — forum topics (one group, many topics)

1. [ ] Create a supergroup, enable **Topics**, add the bot.
2. [ ] Create one topic per repo (manually — the relay does not create topics).
3. [ ] Inside topic *Mycelium*: `/project bind mycelium`
4. [ ] Expect: `✅ Bound project mycelium … chat_id=<neg> thread_id=<n>`
5. [ ] Confirm overlay: `.chats.d/bindings.json` has that `chat_id` +
      `thread_id` + `"project":"mycelium"` (gitignored).
6. [ ] In the same topic: `/project here` → `match_kind=chat`, project
      `mycelium`.
7. [ ] Send `@grok status` (or default backend text) → sticky project kept;
      backend from prefix or project/global default.
8. [ ] In a **different** topic (unbound): message must **not** inherit the
      mycelium sticky bind.

### Pattern B — separate group per project

1. [ ] Create a group for the repo, add the bot.
2. [ ] In that group (no topic / General): `/project bind mycelium`
3. [ ] Expect bind with `thread_id=none` (overlay stores `"thread_id": null`).
4. [ ] `/project here` → project `mycelium`, `match_kind=chat`.
5. [ ] Prefix still works: `@claude …` keeps sticky project, switches backend.

### Bind / unbind / list

| Command | Expect |
|---|---|
| `/project` or `/project list` | Lists `[projects.*]` + bound rooms (static + overlay). |
| `/project bind <slug>` | Upserts overlay row for **this** `chat_id`+`thread_id`. Re-bind same room with a new slug replaces the project. |
| `/project unbind` | Removes **only** this room’s overlay row (static `[[chats]]` unchanged). |
| `/project here` | Shows `backend\|project\|text\|kind` for the current room. |

Negative supergroup `chat_id` values (e.g. `-100…`) must work. Missing
overlay is created on first successful bind; a **corrupt** overlay is
refused (not silently wiped) — fix or delete `.chats.d/bindings.json`.

### Sticky project + prefix

- [ ] Project-only bind (`backend` unset): room is sticky on **project**;
      unprefixed text uses `[projects.<slug>].default_backend` or
      `[routing].default_backend`.
- [ ] Prefixed text inside the room (`@grok …`) changes **backend** only;
      project stays the bound slug.
- [ ] Unified DM (no sticky `[[chats]]` / overlay row): prefixes route
      backend as usual; no forced project unless backend default sets one.

### Hook reverse-lookup (adapters)

Hooks set `RELAY_PROJECT` from cwd via `project_from_cwd`, then
`relay-notify.sh` reverse-looks up the room with `route_lookup_chat`:

1. [ ] Working tree under `[projects.mycelium].root` (or a configured
      worktree path).
2. [ ] Fire a hook (Claude / Grok) with no explicit `RELAY_CHAT_ID`.
3. [ ] Ping lands in the bound forum topic or group for `mycelium`, not
      only the legacy `ALLOWED_CHAT_ID`.

### Overlay merge order (regression)

Effective chats = static `[[chats]]` **minus** any row whose
`chat_id|thread_id` key appears in `.chats.d/bindings.json`, **plus** all
overlay rows. Overlay wins on conflict; other static rows stay.

Offline tests: `tests/test_project_bind.py`, `tests/test_routing_tables.py`,
and the `/project` bind section in `tests/run-tests.sh`.

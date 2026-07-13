# Provider extensions

Universal **platform / provider / model** support is implemented as a
registry of provider extensions under [`providers/`](../providers/).

Each extension can contribute:

| Capability | How |
|---|---|
| **Hooks** | Event catalog, name normalization, payload → summary formatting |
| **Usage** | Transcript collector registered as a `usage_ingest` source |
| **Routing** | `backend_id` for multi-backend rooms / tags |
| **Model inference** | `model_prefixes` → `provider_label` (anthropic, xai, ollama, …) |

## Registered providers

| id | Platform | Hooks | Usage source | Config namespace |
|---|---|---|---|---|
| `grok` | Grok Build / Grok CLI | **14/14** documented | `grok` | `[grok.*]` |
| `claude` | Claude Code | **30/30** documented (`providers/claude/hooks.py`; shell adapter still default) | `claude-code` | `[claude_code.*]` |
| `ollama` | Ollama (+ llama.cpp backends) | (delivery via backends; hooks N/A) | `ollama` (stub — no local logs) | `[ollama]` / `[backends.*]` |
| `generic` | Free-text / label | via `relay-notify` | — | `[generic]` |

```bash
# Prefer python3.14 (see lib/python.sh); or: source lib/python.sh && relay_python …
python3.14 lib/provider_catalog.py list   # or python3 if 3.14 not on PATH
python3.14 lib/provider_catalog.py events grok
python3.14 lib/provider_catalog.py usage-sources
```

## Grok Build (complete)

All documented Grok hook events are implemented in
[`providers/grok/hooks.py`](../providers/grok/hooks.py):

SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PostToolUseFailure,
PermissionDenied, Stop, StopFailure, Notification, SubagentStart,
SubagentStop (+ SubagentEnd alias), PreCompact, PostCompact, SessionEnd.

Plus Cursor aliases (`preToolUse`, `beforeShellExecution`, …).

**Install:**

```bash
# Optional: enable more events in relay.toml [grok.<Event>] enabled = true
bash install-grok-hooks.sh --dry-run
bash install-grok-hooks.sh
```

**Runtime path:**

```text
Grok hook → hook-notify-grok.sh → adapters/grok.sh
  → lib/provider_hook.py grok  (providers/grok)
  → relay-notify.sh --raw  (TG_SEND_SOURCE=hook, RELAY_BACKEND=grok)
```

**Usage:**

```toml
[usage]
enabled = true
source = "multi"   # or "grok" alone
```

Grok tokens are a **context-peak proxy** (local sessions), labeled honestly.

## Ollama / llama.cpp (delivery + usage stub)

Self-hosted models are **backends**, not hook harnesses. There is no
Claude/Grok-style lifecycle event stream. The relay delivers inbound
Telegram text into a local CLI/HTTP process via multi-backend routing
(`delivery = "cmd"`), then you (or a wrapper script) send replies back
through `relay-notify.sh` / `tg-send.sh` if you want them in chat.

This is **not** a full Ollama runtime integration: the poll loop only
fire-and-forgets your `cmd`; it does not manage the model server, stream
tokens into Telegram, or install hooks.

Full routing concepts: [`docs/ROUTING.md`](ROUTING.md). Example tables
also live in `relay.toml.example` under `[backends.ollama]` /
`[backends.llamacpp]`.

### How delivery works

1. Define a backend in `relay.toml` with `delivery = "cmd"` and a `cmd`
   that reads the relay inject environment.
2. Route traffic with sticky `[[chats]]` and/or message prefixes
   (`@ollama …`, `@llama …`).
3. `tg-poll.sh` resolves backend + project, exports `RELAY_*`, and runs
   `cmd` in the background (never blocks the poll loop). Optional
   `RELAY_CWD` comes from `[projects.<id>.worktrees.<backend>]`.

**Env vars set for `cmd` delivery** (from `tg-poll.sh`):

| Variable | Meaning |
|---|---|
| `RELAY_TEXT` | Inbound user message text |
| `RELAY_BACKEND` | Backend id (`ollama`, `llamacpp`, …) |
| `RELAY_PROJECT` | Project slug (if bound / defaulted) |
| `RELAY_CHAT_ID` | Originating Telegram chat id |
| `RELAY_THREAD_ID` | Forum topic thread id (if any) |
| `RELAY_CWD` | Per-backend worktree path (may be empty) |
| `RELAY_MODEL` | `[backends.<id>].model` (e.g. `llama3.2`) |

### Example: Ollama CLI

```toml
[routing]
default_backend = "ollama"
require_prefix = false
tag_style = "bracket"

[backends.ollama]
type = "ollama"
delivery = "cmd"
model = "llama3.2"
# cmd is a JSON array or string; the shell form may reference RELAY_* env.
cmd = ["bash", "-lc", "ollama run \"$RELAY_MODEL\" \"$RELAY_TEXT\""]
tag = "ollama"
prefixes = ["@ollama", "/ollama", "ollama:"]
project = "mycelium"
```

`ollama run` prints to the process stdout, which the poller discards
(`>/dev/null`). To land model output in Telegram, wrap the CLI so the
reply is piped into the bridge, for example:

```toml
cmd = [
  "bash", "-lc",
  '''out=$(ollama run "$RELAY_MODEL" "$RELAY_TEXT")
     RELAY_BACKEND=ollama RELAY_PROJECT="$RELAY_PROJECT" \
       "$HOME/.claude/telegram-bridge/relay-notify.sh" --raw "$out"'''
]
```

(Adjust the bridge path to your install; `go-live.sh` /
`scripts/deploy-local.sh` document the default.)

### Example: llama.cpp HTTP server

```toml
[backends.llamacpp]
type = "llamacpp"
delivery = "cmd"
model = "local-gguf"
tag = "llamacpp"
prefixes = ["@llama", "/llama", "llamacpp:"]
# curl the OpenAI-compatible or native completion endpoint; still fire-and-forget.
cmd = [
  "bash", "-lc",
  '''curl -sS http://127.0.0.1:8080/completion \
       -H 'Content-Type: application/json' \
       -d "$(jq -n --arg p "$RELAY_TEXT" '{prompt:$p,n_predict:256}')" \
     | jq -r '.content // .content[0] // empty' \
     | RELAY_BACKEND=llamacpp "$HOME/.claude/telegram-bridge/relay-notify.sh" --raw'''
]
```

No separate `providers/llamacpp` package is required: routing is entirely
`[backends.llamacpp]`. Model-id prefixes for local families still map to
`provider_label = "ollama"` via the registered Ollama provider when usage
rows exist later.

### Provider registry entry

```text
providers/ollama/
  __init__.py   # Provider id=ollama, backend_id=ollama
  usage.py      # stub collector (issue #33)
```

- **Hooks:** empty catalog (N/A).
- **Usage source:** `ollama` — registered so
  `lib/provider_catalog.py usage-sources` and `usage_ingest.ADAPTERS`
  know the name.
- **Model prefixes:** `llama`, `mistral`, `qwen`, `phi`, `gemma`,
  `codellama` → label `ollama`.

### Usage stub (honest empty)

Ollama and llama.cpp leave **no** Claude/Grok-style token transcripts on
disk for this relay to scrape. The collector in
[`providers/ollama/usage.py`](../providers/ollama/usage.py) therefore:

1. Never invents token counts.
2. Raises `NoLocalUsageLogs` so `usage_ingest.collect` records an honest
   `skipped` reason (`ollama: collection error: NoLocalUsageLogs`) when
   `[usage].source = "ollama"`.

```toml
[usage]
enabled = true
# Prefer real harness logs for dashboards:
# source = "multi"          # claude-code + grok
# source = "claude-code"
# source = "grok"
#
# Explicit ollama source is valid but always skips until a log adapter exists:
# source = "ollama"
# projects_dir = "~/.ollama"   # optional; not usage transcripts today
```

Canonical reason string (stable for docs/greps):

```text
no local usage logs (ollama/llama.cpp leave no token transcripts for the relay)
```

When real local logs become available, implement parsing in
`collect_usage(base)` and stop raising — no changes required in
`usage.sh` / the dashboard beyond the existing registry path.

## Adding a provider

1. Create `providers/<id>/__init__.py` registering a `Provider(...)`.
2. Optional `hooks.py` with `EVENTS`, `normalize_event`, `format_hook`.
3. Optional `usage.py` with `collect_usage(base: Path)`.
4. Import the package from `providers/__init__.py`.
5. Thin shell adapter (optional): call `lib/provider_hook.py <id>`.
6. Document config namespace in `relay.toml.example`.

Consumers (usage dashboard, routing, install scripts) discover extensions
only through the registry — no hard-coded per-platform branches required
for new sources beyond registration.

# Provider extensions (plug-and-play)

Universal **platform / provider / model** support is a registry under
[`providers/`](../providers/). **Grok** and **Claude Code** are the full hook
harnesses; **OpenAI / ChatGPT**, **Gemini**, **Ollama**, **Aider**, and any
OpenAI-compatible self-hosted stack plug in as **delivery backends** (and
optional usage adapters) without changing the relay core.

## Two integration shapes

| Shape | Who | What the relay does |
|---|---|---|
| **Hook harness** | Claude Code, Grok Build | Lifecycle events → Telegram (status pings, TTS) |
| **Delivery backend** | OpenAI, Ollama, vLLM, LM Studio, Gemini, Aider, … | Telegram message → agent/CLI/HTTP → optional reply via `relay-notify` |

Most “ChatGPT / self-hosted model” products are **delivery** only — they have
no Claude/Grok-style global hook bus. That is expected and supported.

## Capabilities

| Capability | Meaning |
|---|---|
| `hooks` | Event catalog + `format_hook` |
| `usage` | `usage_collect` for the dashboard |
| `delivery` | Multi-backend routing (`[backends.*]`) |
| `openai_compat` | Speaks OpenAI Chat Completions HTTP (vLLM, LM Studio, OpenRouter, …) |

## Registered providers

| id | Platform | Caps | Config |
|---|---|---|---|
| `grok` | Grok Build | hooks + usage + delivery | `[grok.*]` |
| `claude` | Claude Code | hooks + usage + delivery | `[claude_code.*]` |
| `openai` | ChatGPT API + OpenAI-compatible self-host | delivery + usage + openai_compat | `[openai]` / `[backends.*]` |
| `ollama` | Ollama + llama.cpp | delivery + usage | `[ollama]` / `[backends.*]` |
| `gemini` | Google Gemini API | delivery | `[gemini]` |
| `aider` | Aider coding agent | delivery | `[aider]` |
| `generic` | Free-text / label | delivery | `[generic]` |

```bash
source lib/python.sh   # or: uv run python
relay_python lib/provider_catalog.py list
relay_python lib/provider_catalog.py capabilities
relay_python lib/provider_catalog.py presets openai
relay_python lib/provider_catalog.py backend-type vllm
relay_python lib/provider_catalog.py usage-sources
```

## Plug-and-play: add a platform in minutes

```bash
# Scaffold
bash scripts/new-provider.sh mytool --with-usage   # optional --with-hooks

# Edit providers/mytool/__init__.py (prefixes, cmd preset, model_prefixes)
# Auto-discovered — no need to edit providers/__init__.py for new folders

relay_python lib/provider_catalog.py list
# Copy a delivery preset into relay.toml [backends.mytool]
```

1. Create `providers/<id>/` with `register(Provider(...))`.
2. Optional `hooks.py` / `usage.py`.
3. Drop-in discovery via `discover_and_import()` (already called on import).
4. Add `[backends.<id>]` from a **delivery preset** (or invent your own `cmd`).
5. Route with prefixes (`@mytool …`) or sticky `[[chats]]` / `/project bind`.

No core relay changes required for a new delivery platform.

## OpenAI / ChatGPT / OpenAI-compatible (self-hosted)

Provider id: **`openai`**. Backend types owned:

`openai`, `chatgpt`, `openai-compat`, `openrouter`, `lmstudio`, `vllm`,
`litellm`, `localai`, `azure-openai`.

### ChatGPT API (cloud)

```toml
[routing]
default_backend = "openai"

[backends.openai]
type = "openai"
delivery = "cmd"
model = "gpt-4o-mini"
tag = "openai"
prefixes = ["@openai", "@chatgpt", "openai:", "chatgpt:"]
# Requires OPENAI_API_KEY in the poll process environment
cmd = ["bash", "-lc", "openai api chat.completions.create -m \"$RELAY_MODEL\" -g user \"$RELAY_TEXT\" | RELAY_BACKEND=openai \"$HOME/.claude/telegram-bridge/relay-notify.sh\" --raw"]
```

### Self-hosted / any OpenAI-compatible server

Works for **vLLM, LM Studio, LiteLLM, LocalAI, Ollama’s OpenAI shim,
text-generation-webui, OpenRouter**, etc.:

```toml
[backends.local]
type = "openai-compat"    # or lmstudio / vllm / openrouter
delivery = "cmd"
model = "local-model"
tag = "local"
prefixes = ["@local", "@oai"]
# OPENAI_BASE_URL=http://127.0.0.1:1234/v1  OPENAI_API_KEY=lm-studio
cmd = [
  "bash", "-lc",
  '''curl -sS "${OPENAI_BASE_URL:-http://127.0.0.1:1234/v1}/chat/completions" \
       -H "Content-Type: application/json" \
       -H "Authorization: Bearer ${OPENAI_API_KEY:-lm-studio}" \
       -d "$(jq -n --arg m "${RELAY_MODEL:-local-model}" --arg p "$RELAY_TEXT" \
            '{model:$m,messages:[{role:"user",content:$p}]}')" \
     | jq -r '.choices[0].message.content // empty' \
     | RELAY_BACKEND=local "$HOME/.claude/telegram-bridge/relay-notify.sh" --raw'''
]
```

List built-in presets:

```bash
relay_python lib/provider_catalog.py presets openai
```

**Usage:** cloud token dashboards are not scraped. `source = "openai"` is a
known adapter that **honestly skips** until a local export/Codex log parser
exists (same pattern as Ollama).

## Gemini / Aider

Same delivery model — see `providers/gemini` and `providers/aider` presets:

```bash
relay_python lib/provider_catalog.py presets gemini
relay_python lib/provider_catalog.py presets aider
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

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
| `adk` | Google Agent Development Kit (optional) | delivery | `[adk]` — see [ADK_MCP.md](ADK_MCP.md) |
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

## Grok Build Telegram hooks

All **14** documented Grok Build hook events are implemented in
[`providers/grok/hooks.py`](../providers/grok/hooks.py) (catalog matches
official semantics in `~/.grok/docs/user-guide/10-hooks.md`). Cursor
aliases (`preToolUse`, `beforeShellExecution`, …) and `SubagentEnd` →
`SubagentStop` are accepted.

Day-one install steps also live in [`SETUP.md`](../SETUP.md) (adapter
step). Quality bar / epic notes: [`GROK_HOOKS.md`](GROK_HOOKS.md).
Example config comments: [`relay.toml.example`](../relay.toml.example)
(`[grok.*]` quiet vs full).

### Install, trust, reload, verify

1. **(Optional) choose quiet or full** in `relay.toml` — see [profiles](#quiet-vs-full-profiles) below. Without a `relay.toml`, catalog defaults apply (quiet).
2. **Install hooks** (catalog-driven; only writes `~/.grok/hooks/tg-agent-relay.json`):
   ```bash
   bash install-grok-hooks.sh --dry-run
   bash install-grok-hooks.sh
   # reverse: bash install-grok-hooks.sh --uninstall
   ```
3. **Folder trust** — required only for **project** hooks under
   `<repo>/.grok/hooks/` (or project Claude/Cursor hook files Grok scans).
   Global `~/.grok/hooks/*.json` (what the installer writes) is always
   trusted. Grant project trust with `/hooks-trust` or `--trust`; decision
   is stored in `~/.grok/trusted_folders.toml`. Untrusted project hooks
   are skipped silently.
4. **Restart the Grok session** so the runner reloads hook files. Check
   the Hooks tab (`Ctrl+L`, or `/hooks` on VS Code family).
5. **Verify Stop** — complete a short agent turn; expect a Telegram line
   with the Stop prefix (`🏁` by default). That confirms install + token
   + allowlist + session reload in one shot.

### Runtime path

```text
Grok hook → hook-notify-grok.sh → adapters/grok.sh
  → lib/provider_hook.py grok  (providers/grok)
  → relay-notify.sh --raw  (TG_SEND_SOURCE=hook, RELAY_BACKEND=grok)
```

Grok may also invoke `hook-notify.sh` when Claude-compat settings are
scanned; that shim detects `GROK_*` / `hookEventName` and re-dispatches
to `adapters/grok.sh` so Grok is not formatted as Claude.

### All 14 events (default enabled)

| Event | Default | Typical signal |
|---|---|---|
| `SessionStart` | off | Session begins |
| `UserPromptSubmit` | off | User submitted a prompt |
| `PreToolUse` | off | Tool about to run (notify-only here; Grok can block via other hooks) |
| `PostToolUse` | off | Tool succeeded (high volume) |
| `PostToolUseFailure` | **on** | Tool failed |
| `PermissionDenied` | off | Permission system denied a tool |
| `Stop` | **on** | Agent turn ended |
| `StopFailure` | **on** | Turn ended on API error |
| `Notification` | **on** | Agent notification |
| `SubagentStart` | off | Subagent started |
| `SubagentStop` | **on** | Subagent finished (`SubagentEnd` alias) |
| `PreCompact` | off | Compaction about to run |
| `PostCompact` | off | Compaction finished |
| `SessionEnd` | off | Session ended |

Defaults come from the provider catalog (`providers/grok`); each may be
overridden with `[grok.<Event>].enabled` in `relay.toml`, then re-run
`install-grok-hooks.sh`.

### Quiet vs full profiles

**Quiet** (current defaults) keeps phone noise low: lifecycle failures and
turn/subagent completion only. Prefer this until you know you want more.

**Full** turns on useful session/subagent/permission/compact events for
higher fidelity without enabling every tool call. Leave `PreToolUse` /
`PostToolUse` off unless you deliberately want a ping per tool.

Trade-off in one line: quiet optimizes for signal density on a phone;
full trades volume for visibility into session boundaries and denials.
(Design notes style: [`DECISIONS.md`](DECISIONS.md).)

**Quiet** — no `relay.toml` needed, or mirror catalog defaults:

```toml
# Quiet profile (defaults — install-grok-hooks.sh enables these five)
[grok.PostToolUseFailure]
enabled = true
[grok.Stop]
enabled = true
[grok.StopFailure]
enabled = true
[grok.Notification]
enabled = true
[grok.SubagentStop]
enabled = true
```

**Full** — recommended extras on top of quiet:

```toml
# Full profile: quiet five + session / subagent / permission / compact
[grok.PostToolUseFailure]
enabled = true
[grok.Stop]
enabled = true
[grok.StopFailure]
enabled = true
[grok.Notification]
enabled = true
[grok.SubagentStop]
enabled = true

[grok.SessionStart]
enabled = true
[grok.SessionEnd]
enabled = true
[grok.SubagentStart]
enabled = true
[grok.PermissionDenied]
enabled = true
[grok.PreCompact]
enabled = true
[grok.PostCompact]
enabled = true

# Still opt-in only (very high volume):
# [grok.PreToolUse]
# enabled = true
# [grok.PostToolUse]
# enabled = true
# [grok.UserPromptSubmit]
# enabled = true
```

After editing, re-run `bash install-grok-hooks.sh` and restart Grok.

### Matchers (optional)

Filter which tool names fire a notify hook without disabling the whole event.
Config key is exactly `matcher` under `[grok.<Event>]`:

```toml
[grok.PreToolUse]
enabled = true
matcher = "Shell|Bash"   # Grok-native regex; empty/absent = match all
```

`install-grok-hooks.sh` emits `"matcher"` into the hooks JSON when set;
omits the field when empty/absent (match all — prior behavior). Dry-run
prints any matchers in the plan. Tool events (`PreToolUse`, `PostToolUse`,
`PostToolUseFailure`, `PermissionDenied`) and `Notification` support
matchers; lifecycle events reject them. **Notify-only** remains — no
PreToolUse deny/policy engine in this bridge.

### Troubleshooting

| Symptom | Likely cause | What to check |
|---|---|---|
| Ping looks like **Claude “event: unknown”** (or wrong adapter) | Grok payload reached the Claude formatter | Prefer `install-grok-hooks.sh` → `hook-notify-grok.sh`. If only Claude-compat hooks fire, ensure `hook-notify.sh` is current (it re-dispatches on `GROK_*` / `hookEventName`). Avoid pointing Grok events at a stale path that only runs `adapters/claude-code.sh`. |
| **Hooks not firing** | No install, no session reload, project untrusted, or event disabled | `ls ~/.grok/hooks/tg-agent-relay.json`; re-run installer; **restart Grok**; for project hooks, `/hooks-trust`; confirm event `enabled` + installer plan lists it; Hooks tab should show the command. |
| **Wrong chat** / tagged as another backend | Multi-backend routing or sticky binding | Hooks set `RELAY_BACKEND=grok` and may resolve `RELAY_PROJECT` from cwd. Check `[[chats]]` / `/project bind` and `[backends.grok]` in [`ROUTING.md`](ROUTING.md). Unified DM without a sticky bind uses allowlist `ALLOWED_CHAT_ID`. |
| Install no-op but you expected a change | `relay.toml` still has old `enabled` flags | Edit `[grok.<Event>]`, dry-run, install again. |
| Silent skip | Missing token / allowlist, or Python provider path missing | `.env` (`BOT_TOKEN`, `ALLOWED_USER_ID`/`ALLOWED_CHAT_ID`); bridge metrics `grok_skip`; `python3` + `lib/provider_hook.py` present. |

### Official Grok hooks reference

On a machine with Grok Build installed:

```text
~/.grok/docs/user-guide/10-hooks.md
```

That document is the source for event names, blocking vs passive,
matchers, trust, and stdin JSON. This relay catalogs all 14 and defaults
most high-volume events off.

### Usage (token dashboard)

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

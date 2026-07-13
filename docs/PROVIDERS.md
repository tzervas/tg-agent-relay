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
| `ollama` | Ollama | (delivery via backends; hooks N/A) | — | `[ollama]` / backends |
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

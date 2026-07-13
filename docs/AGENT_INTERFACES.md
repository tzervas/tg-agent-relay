# Agent interfaces (swarm join contracts)

Orchestrator-owned APIs. **Implement against these — do not invent parallel shapes.**

Python: **3.14** via **uv** + **ruff** (see [`docs/TOOLING.md`](TOOLING.md)).  
Runtime fallback: `lib/python.sh` / `RELAY_PYTHON` (prefers `.venv` from `uv sync`).

```bash
uv sync --all-groups
uv run ruff check --fix <paths>
uv run ruff format <paths>
bash scripts/dev.sh check
```

Rust: full toolchain via `rust-toolchain.toml` (**MSRV 1.96** + rustfmt/clippy/rust-src/rust-analyzer).

## Package

```
tg_agent_relay/
  protocols.py   # RouteResult, FormatResult, SendRequest, Protocols
  config.py      # load_config, cfg_get
  routing.py     # resolve → RouteResult
  metrics.py     # emit_metric
  tts.py         # strip_formatting
  hooks.py       # dispatch_hook(provider_id, payload) → (OK|SKIP, body)
  format_api.py  # format_message stub (#25 fills in)
  cli.py         # tg-relay version | hook | route
providers/       # provider extensions (hooks + usage)
```

## Quick use

```bash
source lib/python.sh
relay_python -c "from tg_agent_relay import __version__; print(__version__)"
relay_python -m tg_agent_relay.cli version
printf '%s' '{"hookEventName":"stop","message":"hi"}' \
  | relay_python -m tg_agent_relay.cli hook grok
```

## Issue → module map

| Issue | Module to fill | Protocol |
|---|---|---|
| #24 routing | `tg_agent_relay/routing.py` (+ tests) | `Router` |
| #25 format | `tg_agent_relay/format_api.py` | `Formatter` |
| #26 send | `tg_agent_relay/send.py` (create) | `Sender` |
| #27 poll | `tg_agent_relay/poll.py` (create) | uses Router + handlers |
| #30 Claude hooks | `providers/claude/hooks.py` | `format_hook` on Provider |
| #31 usage registry | `lib/usage_ingest.py` + providers | `UsageCollector` |
| #40 fixtures | `tests/fixtures/hooks/{grok,claude}/` | JSON stdin samples |

## RouteResult pipe format (stable)

```
backend|project|text|match_kind
```

`match_kind` ∈ `chat` | `prefix` | `default` | `none` | `legacy`

## Hook dispatch (stable)

```python
status, body = dispatch_hook("grok", payload_dict, config=cfg)
# status == "OK" → body is one-line summary for Telegram
# status == "SKIP" → body is reason (disabled, empty, …)
```

## Config dict shape

Same as `relay.toml` → JSON via tomllib:

```json
{
  "routing": { "default_backend": "claude" },
  "backends": { "claude": { "prefixes": ["@claude"], "tag": "claude" } },
  "chats": [{ "chat_id": -1001, "project": "mycelium" }],
  "projects": { "mycelium": { "root": "/path" } },
  "grok": { "Stop": { "enabled": true } },
  "claude_code": { "SubagentStop": { "enabled": true } }
}
```

## Do not

- Change `.env` secrets
- Call live Telegram from unit tests
- Load both visual and text hybrid context for the same id
- Voice: call IDs, URLs, backticks (see `tts.strip_formatting`)

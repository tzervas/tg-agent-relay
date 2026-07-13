# Epics & swarm task board

**Python 3.14** · **uv + ruff** · Rust **MSRV 1.96** · **local-ci only** (remote Actions off).  
Join contracts: [AGENT_INTERFACES.md](AGENT_INTERFACES.md) · Tooling: [TOOLING.md](TOOLING.md) · Release: [RELEASING.md](RELEASING.md)

## Epics

| Epic | Status |
|---:|---|
| [#18](https://github.com/tzervas/tg-agent-relay/issues/18) Shell → Python 3.14 | **Core ports landed** — package format/routing/send/poll/tts; shell remains live entry until cutover |
| [#19](https://github.com/tzervas/tg-agent-relay/issues/19) Providers | **Done** — Grok + Claude + Ollama stub + usage registry + catalog install |
| [#20](https://github.com/tzervas/tg-agent-relay/issues/20) Product polish | **Done** — rooms/voice/context/MCP stub |
| [#21](https://github.com/tzervas/tg-agent-relay/issues/21) Quality / swarm | **Done** — local-ci, fixtures, template, pytest dual-run |
| [#22](https://github.com/tzervas/tg-agent-relay/issues/22) Rust hotspots | Open — MSRV 1.96; #41 spike optional |

## Children

| # | Status |
|---:|---|
| 23–25, 30–33, 35–36, 38–40, 42 | **Closed** |
| 26 send · 27 poll · 28 TTS · 29 pytest · 34 rooms · 37 MCP | **Closed** (integrated) |
| 41 Rust spike | Deferred P2 |

## Package surface (`tg_agent_relay/`)

| Module | Role |
|---|---|
| `format_api` | HTML formatter parity |
| `routing` | multi-backend + project rooms |
| `send` | paginate, flock, TTS, Telegram HTTP |
| `poll` | allowlist, reassembly, commands, route |
| `tts` | strip + short/full spoken prepare |
| `hooks` / `mcp_stub` | provider dispatch + MCP-shaped facade |
| `cli` | `version` · `hook` · `route` · `format` · send/poll entrypoints |

## Quality gate

```bash
bash scripts/local-ci.sh          # 288+ offline asserts
uv run pytest                     # pure Python dual-run
bash scripts/release.sh vX.Y.Z    # local publish
```

Shell `tg-send.sh` / `tg-poll.sh` remain the **default live path** until you opt into Python entrypoints (`tg-relay-send` / `tg-relay-poll` or explicit env). Cutover is a deploy-time choice, not a flag day.

## Optional remaining

- Wire `tg-send.sh` → thin `exec` to `python -m tg_agent_relay.send` behind `RELAY_PYTHON_SEND=1`
- Full code_highlight document queue in Python send
- #41 Rust benchmarks if profiling demands

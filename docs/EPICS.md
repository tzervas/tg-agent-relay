# Epics & swarm task board

**Python 3.14** · **uv + ruff** · Rust **MSRV 1.96** · **local-ci only**.  
Join: [AGENT_INTERFACES.md](AGENT_INTERFACES.md) · [TOOLING.md](TOOLING.md) · [RELEASING.md](RELEASING.md) · [ADK_MCP.md](ADK_MCP.md) · [PROVIDERS.md](PROVIDERS.md)

## Epics

| Epic | Status |
|---:|---|
| [#18](https://github.com/tzervas/tg-agent-relay/issues/18) Shell → Python | **Near done** — ports + opt-in cutover; remaining children #57–#59 |
| [#19](https://github.com/tzervas/tg-agent-relay/issues/19) Providers | **Closed** (+ OpenAI/ADK plug-and-play post-close) |
| [#20](https://github.com/tzervas/tg-agent-relay/issues/20) Product polish | **Closed** |
| [#21](https://github.com/tzervas/tg-agent-relay/issues/21) Quality / swarm | **Closed** |
| [#22](https://github.com/tzervas/tg-agent-relay/issues/22) Rust hotspots | Open — #41 optional |

## Active children (#18)

| # | Task | Status |
|---:|---|---|
| 57 | Python send code_highlight sendDocument | **Landing** |
| 58 | Cutover docs + deploy notes | **Landing** |
| 59 | Claude adapter default provider_hook | **Landing** |
| 41 | Rust spike | Deferred P2 |

## Package surface

`format_api` · `routing` · `send` (+ highlight_docs) · `poll` · `tts` · `hooks` · `mcp_stub` · `extensions` · `adk_bridge` · providers/*

## Cutover

```bash
export RELAY_PYTHON_SEND=1 RELAY_PYTHON_POLL=1
bash scripts/local-ci.sh
bash scripts/deploy-local.sh
```

Shell remains default until these env flags are set (see RELEASING.md).

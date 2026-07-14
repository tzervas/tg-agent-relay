# Epics & swarm task board

**Python 3.14** · **uv + ruff** · Rust **MSRV 1.96** · **local-ci only**.  

**Process:** [WORKFLOW.md](WORKFLOW.md) (orchestrator + swarm, cost lanes, spawn prompts).  
Join APIs: [AGENT_INTERFACES.md](AGENT_INTERFACES.md) · [TOOLING.md](TOOLING.md) · [RELEASING.md](RELEASING.md) · [ADK_MCP.md](ADK_MCP.md) · [PROVIDERS.md](PROVIDERS.md)

## Epics

| Epic | Status |
|---:|---|
| [#18](https://github.com/tzervas/tg-agent-relay/issues/18) Shell → Python | **Near done** — #57–#59 closed; open until live soak / default cutover decision |
| [#19](https://github.com/tzervas/tg-agent-relay/issues/19) Providers | **Closed** (+ OpenAI/ADK plug-and-play post-close) |
| [#20](https://github.com/tzervas/tg-agent-relay/issues/20) Product polish | **Closed** |
| [#21](https://github.com/tzervas/tg-agent-relay/issues/21) Quality / swarm | **Closed** |
| [#22](https://github.com/tzervas/tg-agent-relay/issues/22) Rust hotspots | Open — #41 optional |
| [#60](https://github.com/tzervas/tg-agent-relay/issues/60) **Grok hooks ≥ Claude quality** | **Open** — children #61–#66 · [GROK_HOOKS.md](GROK_HOOKS.md) |

## Active children (#18)

| # | Task | Status |
|---:|---|---|
| 57 | Python send code_highlight sendDocument | **Closed** |
| 58 | Cutover docs + deploy notes | **Closed** |
| 59 | Claude adapter default provider_hook | **Closed** |
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


## Grok hooks epic (#60)

| # | Task | Size | Priority |
|---:|---|---|---|
| 61 | Install Claude-parity idempotent/dry-run | M | P0 |
| 62 | format_hook richer summaries | M | P0 |
| 63 | Fixture + adapter e2e all 14 | M | P1 |
| 64 | Optional matcher in install | S | P2 |
| 65 | SETUP/PROVIDERS quiet vs full profiles | S | P1 |
| 66 | Live smoke checklist + metrics | S | P2 |

Spawn with **Grok Build** agents (see WORKFLOW.md cost lanes). Gap analysis: [GROK_HOOKS.md](GROK_HOOKS.md).

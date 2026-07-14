# Epics & swarm task board

**Python 3.14** · **uv + ruff** · Rust **MSRV 1.96** · **local-ci only**.  

**Process:** [WORKFLOW.md](WORKFLOW.md) (branches: **`main`** stable · **`dev`** integration · `feat/*` off `dev`).  
Join APIs: [AGENT_INTERFACES.md](AGENT_INTERFACES.md) · [TOOLING.md](TOOLING.md) · [RELEASING.md](RELEASING.md) · [ADK_MCP.md](ADK_MCP.md) · [PROVIDERS.md](PROVIDERS.md)

## Epics

| Epic | Status |
|---:|---|
| [#18](https://github.com/tzervas/tg-agent-relay/issues/18) Shell → Python | **Closed** — Python default (#67); shell opt-out |
| [#19](https://github.com/tzervas/tg-agent-relay/issues/19) Providers | **Closed** (+ OpenAI/ADK plug-and-play post-close) |
| [#20](https://github.com/tzervas/tg-agent-relay/issues/20) Product polish | **Closed** |
| [#21](https://github.com/tzervas/tg-agent-relay/issues/21) Quality / swarm | **Closed** |
| [#22](https://github.com/tzervas/tg-agent-relay/issues/22) Rust hotspots | Open — #41 optional / deferred |
| [#60](https://github.com/tzervas/tg-agent-relay/issues/60) Grok hooks ≥ Claude quality | **Closed** — children #61–#66 · [GROK_HOOKS.md](GROK_HOOKS.md) |

## Closed recently (#18 / #60)

| # | Task | Status |
|---:|---|---|
| 57–59, 67 | Python cutover / defaults | **Closed** |
| 61 | Install Claude-parity dry-run/no-op | **Closed** (PR #70) |
| 62 | format_hook richer summaries | **Closed** (PR #71) |
| 63 | Fixture + adapter e2e | **Closed** (PR #74) |
| 64 | Optional matchers in install | **Closed** (PR #73) |
| 65 | Quiet vs full operator docs | **Closed** (PR #69) |
| 66 | Live smoke + metrics | **Closed** (PR #72) |
| 41 | Rust spike | Deferred P2 (epic #22) |

## Package surface

`format_api` · `routing` · `send` (+ highlight_docs) · `poll` · `tts` · `hooks` · `mcp_stub` · `extensions` · `adk_bridge` · providers/*

## Runtime path

```bash
# Default: Python send/poll (falls back to shell if import fails)
bash scripts/local-ci.sh
bash scripts/deploy-local.sh

# Opt out to shell only if needed:
# export RELAY_PYTHON_SEND=0 RELAY_PYTHON_POLL=0
```

See RELEASING.md § Python package path · decisions: [DECISIONS.md](DECISIONS.md).

## Remaining open (optional)

| # | Task | Notes |
|---:|---|---|
| 22 / 41 | Rust hotspots / spike | Only if benchmarks wanted; not blocking product |

Grok operator guide: [GROK_HOOKS.md](GROK_HOOKS.md) · [PROVIDERS.md](PROVIDERS.md).

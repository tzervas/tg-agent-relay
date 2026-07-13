# Epics & swarm task board

Tracking lives on GitHub Issues for `tzervas/tg-agent-relay`.  
**Python target: 3.14** · **uv + ruff** · Rust **MSRV 1.96** · **local-ci first** (remote Actions off).  
**Join contracts:** [docs/AGENT_INTERFACES.md](AGENT_INTERFACES.md).

## Epics

| Epic | Title | Status |
|---:|---|---|
| [#18](https://github.com/tzervas/tg-agent-relay/issues/18) | Shell → Python 3.14 migration | **In progress** — package + format + routing; send/poll ports in flight |
| [#19](https://github.com/tzervas/tg-agent-relay/issues/19) | Universal provider/consumer extensions | **Near done** — Grok + Claude hooks + install catalog + Ollama stub |
| [#20](https://github.com/tzervas/tg-agent-relay/issues/20) | Product polish to 100% | **In progress** — rooms/MCP agents in flight |
| [#21](https://github.com/tzervas/tg-agent-relay/issues/21) | Quality, CI, swarm packaging | **Near done** — local-ci gate; fixtures/template/CI closed |
| [#22](https://github.com/tzervas/tg-agent-relay/issues/22) | Optional Rust hotspots | Deferred — MSRV 1.96; spike #41 later |

## Child issue status

| # | Task | Wave | Status |
|---:|---|---|---|
| 23 | Package scaffold | 0 | **Closed** |
| 24 | Routing port | 1 | **Closed** |
| 25 | Format HTML parity | 2 | **Closed** — package `format_api.py` + tests |
| 26 | tg-send port | 3 | **In progress** (swarm) |
| 27 | tg-poll port | 3 | **In progress** (swarm) |
| 28 | Adapters/TTS package | 3 | **In progress** (swarm) |
| 29 | pytest primary | 3 | **In progress** (swarm) |
| 30 | Claude format_hook | 0 | **Closed** |
| 31 | Usage registry | 1 | **Closed** |
| 32 | Claude install-hooks catalog | 2 | **Closed** |
| 33 | Ollama docs + usage stub | 2 | **Closed** |
| 34 | Project rooms harden | 4 | **In progress** (swarm) |
| 35 | Voice recipe docs | 1 | **Closed** |
| 36 | Hybrid context AGENTS | 1 | **Closed** |
| 37 | MCP facade stub | 4 | **In progress** (swarm) |
| 38 | CI (now local-ci) | 1 | **Closed** (remote manual-only) |
| 39 | Swarm issue template | 0 | **Closed** |
| 40 | Hook fixtures | 0 | **Closed** |
| 41 | Rust spike | 4 | Deferred |
| 42 | Release v0.6.0 | — | **Closed** |

## Local quality (only gate)

```bash
bash scripts/local-ci.sh          # full
bash scripts/release.sh vX.Y.Z    # publish from this workstation
```

Remote GitHub Actions are **disabled / workflow_dispatch only**.

## Waves (execution)

| Wave | Items | State |
|---|---|---|
| 0 | Foundation package | **Done** |
| 1 | #24 #31 #35 #36 #38 | **Done** |
| 2 | #25 #32 #33 | **Done** |
| 3 | #26 #27 #28 #29 | **In flight** |
| 4 | #34 #37 (#41 later) | **In flight** |

## Agent rules

See [AGENT_INTERFACES.md](AGENT_INTERFACES.md). Multi-except **must** be `except (A, B) as _exc:` (ruff 0.15 format bug).

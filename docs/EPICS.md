# Epics & swarm task board

Tracking lives on GitHub Issues for `tzervas/tg-agent-relay`.  
**Python target: 3.14** (3.13 only if hard-required). Prefer pure Python for runtime; Rust only after benchmarks (#22).  
**Tooling:** [docs/TOOLING.md](TOOLING.md) — **uv** + **ruff**; Rust **MSRV 1.96**.  
**Join contracts:** [docs/AGENT_INTERFACES.md](AGENT_INTERFACES.md) (orchestrator-owned).

## Epics

| Epic | Title | Labels | Status |
|---:|---|---|---|
| [#18](https://github.com/tzervas/tg-agent-relay/issues/18) | Shell → Python 3.14 migration | `epic:migration` `P0` | In progress — scaffold landed |
| [#19](https://github.com/tzervas/tg-agent-relay/issues/19) | Universal provider/consumer extensions | `epic:providers` `P0` | In progress — Grok complete; Claude ~done |
| [#20](https://github.com/tzervas/tg-agent-relay/issues/20) | Product polish to 100% | `epic:product` `P1` | Open (rooms/voice/context harden) |
| [#21](https://github.com/tzervas/tg-agent-relay/issues/21) | Quality, CI, swarm packaging | `epic:quality` `P1` | In progress — template/fixtures/CI partial |
| [#22](https://github.com/tzervas/tg-agent-relay/issues/22) | Optional Rust hotspots | `epic:migration` `rust` `P2` | Deferred — MSRV 1.96 pinned |

## Child issue status

| # | Task | Size | Wave | Status |
|---:|---|---|---|---|
| 23 | Package scaffold `tg_agent_relay` + pyproject 3.14 | S | 0 | **Closed** (cfaf364) |
| 24 | Port routing to package | M | 1 | **PR [#45](https://github.com/tzervas/tg-agent-relay/pull/45)** |
| 25 | Port format.sh → Python | L | 2 | Stub `format_api.py` — ready for Wave 2 |
| 26 | Port tg-send → Python | L | 3 | Skeleton `send.py` |
| 27 | Port tg-poll → Python | L | 3 | Not started |
| 28 | Adapters/TTS pure Python | M | 3 | Partial |
| 29 | pytest migration | M | 3 | Growing suite; bash still primary |
| 30 | Claude format_hook in providers | L | 0 | **Closed** |
| 31 | Usage collectors only via registry | S | 1 | **PR [#47](https://github.com/tzervas/tg-agent-relay/pull/47)** |
| 32 | Claude install-hooks via catalog | M | 2 | Open — agent-context posted |
| 33 | Ollama/llama docs stub | S | 2 | Open — agent-context posted |
| 34 | Project rooms harden + e2e | M | 4 | Open — agent-context posted |
| 35 | Voice full-mode recipe | S | 1 | **PR [#46](https://github.com/tzervas/tg-agent-relay/pull/46)** |
| 36 | Hybrid context AGENTS snippet | S | 1 | **PR [#44](https://github.com/tzervas/tg-agent-relay/pull/44)** |
| 37 | MCP facade stub | M | 4 | Open P2 — agent-context posted |
| 38 | CI Python 3.14 | M | 1 | **PR [#43](https://github.com/tzervas/tg-agent-relay/pull/43)** |
| 39 | Swarm issue template | S | 0 | **Closed** |
| 40 | Hook JSON fixture pack | M | 0 | **Closed** |
| 41 | Rust spike benchmarks | M | 4 | Deferred — agent-context posted |
| 42 | Release v0.6.0 | S | — | **Closed** |

## Recommended execution order (for swarms)

### Wave 0 — orchestrator (gate)

Commit foundation; close #23/#39/#40/(#30); post agent-context comments; freeze `protocols.py`.

### Wave 1 — foundation (parallel S/M)

| # | Task | Size | Exclusive write |
|---:|---|---|---|
| 24 | Port routing to package | M | `tg_agent_relay/routing.py`, `lib/routing.py`, `tests/test_routing*.py` |
| 31 | Usage collectors only via registry | S | `lib/usage_ingest.py`, `providers/*/usage.py`, `tests/test_usage_*.py` |
| 38 | CI Python 3.14 + offline tests | M | `.github/workflows/**`, README badge |
| 35 | Voice defaults recipe | S | README / SETUP / `relay.toml.example` TTS only |
| 36 | Hybrid context AGENTS + PNGs | S | `docs/assets/context/**`, AGENTS snippet |

**Barrier B1:** #24 API frozen before Wave 2 large ports.

### Wave 2 — core ports (after B1)

| # | Task | Size | Exclusive write |
|---:|---|---|---|
| 25 | Port format.sh → Python | L | `tg_agent_relay/format_api.py`, `format_*.py`, `tests/test_format*.py` |
| 30 | Claude hooks (if not closed) | L | `providers/claude/hooks.py`, adapter dispatch |
| 32 | Claude install-hooks via catalog | M | Claude install path, catalog touch |
| 33 | Ollama/llama docs stub | S | `providers/ollama/**`, docs |

**Barrier B2:** #25 `FormatResult` stable before #26 HTML pages.

### Wave 3 — send/poll/adapters

| # | Task | Size | Exclusive write |
|---:|---|---|---|
| 26 | Port tg-send → Python | L | `tg_agent_relay/send.py`, `tests/test_send*.py` |
| 27 | Port tg-poll → Python | L | `tg_agent_relay/poll.py`, `tests/test_poll*.py` |
| 28 | Adapters/TTS pure Python | M | `tg_agent_relay/tts.py`, thin adapter shims |
| 29 | pytest migration | M | `tests/**`, pytest config |

**Barrier B3:** #26+#27 offline green before claiming send/poll shell eliminated.

### Wave 4 — optional / polish

| # | Task | Size |
|---:|---|---|
| 34 | Project rooms harden + e2e | M |
| 37 | MCP facade stub | M |
| 41 | Rust spike benchmarks (MSRV 1.96) | M |

## Labels for agents

| Label | Meaning |
|---|---|
| `swarm-ready` | Has user story, AC, API/files, fixtures hints |
| `size:S` / `M` / `L` | Effort hint for small / medium / large agents |
| `P0` / `P1` / `P2` | Priority |
| `python` / `rust` | Language lane |
| `epic:*` | Epic bucket |

## Agent prompt skeleton

```text
You implement ONLY GitHub issue #N for tzervas/tg-agent-relay.
Read the issue body + latest “Agent context” comment.
Read docs/AGENT_INTERFACES.md and docs/TOOLING.md.
Python 3.14 via uv / RELAY_PYTHON. Ruff only for lint/format.
Write ONLY owned paths. Do not change protocols.py shapes.
Do not expand scope. Run listed tests offline.
Open a PR linking Fixes #N.
```

## Orchestrator-owned (do not edit in swarm PRs)

- `tg_agent_relay/protocols.py`
- `docs/AGENT_INTERFACES.md`
- `docs/EPICS.md` (status updates by orchestrator)
- Release scripts / VERSION

## Already landed (not re-issued)

- Grok full 14-event provider + `providers/` registry
- Multi-backend routing + project bind overlay
- Voice spoken_mode short/full + collapse refs
- Hybrid context_select exclusive vision/text
- usage recursive Claude + multi windows
- Python 3.14 resolver (`lib/python.sh`)
- uv + ruff + MSRV 1.96 toolchain pins
- Package scaffold + join protocols + hook fixtures (Wave 0)

## Releases

See [docs/RELEASING.md](RELEASING.md). Tools: `scripts/release.sh`, `scripts/deploy-local.sh`.

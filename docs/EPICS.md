# Epics & swarm task board

Tracking lives on GitHub Issues for `tzervas/tg-agent-relay`.  
**Python target: 3.14** (3.13 only if hard-required). Prefer pure Python for runtime; Rust only after benchmarks (#22).

## Epics

| Epic | Title | Labels |
|---:|---|---|
| [#18](https://github.com/tzervas/tg-agent-relay/issues/18) | Shell → Python 3.14 migration | `epic:migration` `P0` |
| [#19](https://github.com/tzervas/tg-agent-relay/issues/19) | Universal provider/consumer extensions | `epic:providers` `P0` |
| [#20](https://github.com/tzervas/tg-agent-relay/issues/20) | Product polish to 100% | `epic:product` `P1` |
| [#21](https://github.com/tzervas/tg-agent-relay/issues/21) | Quality, CI, swarm packaging | `epic:quality` `P1` |
| [#22](https://github.com/tzervas/tg-agent-relay/issues/22) | Optional Rust hotspots | `epic:migration` `rust` `P2` |

## Recommended execution order (for swarms)

### Wave 1 — foundation (parallel S/M)
| # | Task | Size |
|---:|---|---|
| 23 | Package scaffold `tg_agent_relay` + pyproject 3.14 | S |
| 24 | Port routing to package | M |
| 31 | Usage collectors only via registry | S |
| 39 | Swarm issue template | S |
| 40 | Hook JSON fixture pack | M |

### Wave 2 — core ports (after #23)
| # | Task | Size |
|---:|---|---|
| 25 | Port format.sh → Python | L |
| 26 | Port tg-send → Python | L |
| 27 | Port tg-poll → Python | L |
| 30 | Claude format_hook in providers | L |
| 32 | Claude install-hooks via catalog | M |

### Wave 3 — product + quality
| # | Task | Size |
|---:|---|---|
| 28 | Adapters/TTS pure Python | M |
| 29 | pytest migration | M |
| 34 | Project rooms harden + e2e | M |
| 35 | Voice full-mode recipe | S |
| 36 | Hybrid context AGENTS snippet | S |
| 38 | CI Python 3.14 | M |

### Wave 4 — optional
| # | Task | Size |
|---:|---|---|
| 33 | Ollama/llama docs stub | S |
| 37 | MCP facade stub | M |
| 41 | Rust spike benchmarks | M |

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
Read the issue body (user story, AC, API, out of scope).
Python 3.14 preferred (lib/python.sh / RELAY_PYTHON).
Do not expand scope. Run relevant tests offline.
Open a PR linking Fixes #N.
```

## Already landed (not re-issued)

- Grok full 14-event provider + `providers/` registry
- Multi-backend routing + project bind overlay
- Voice spoken_mode short/full + collapse refs
- Hybrid context_select exclusive vision/text
- usage recursive Claude + multi windows
- Python 3.14 resolver (`lib/python.sh`)

## Releases

See [docs/RELEASING.md](RELEASING.md). Tools: `scripts/release.sh`, `scripts/deploy-local.sh`.

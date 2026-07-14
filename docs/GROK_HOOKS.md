# Grok Build hooks — quality bar vs Claude

**Epic:** [#60](https://github.com/tzervas/tg-agent-relay/issues/60)  
**Process:** [WORKFLOW.md](WORKFLOW.md) (swarm with **Grok Build**, not flagship 4.5)  
**Official events:** `~/.grok/docs/user-guide/10-hooks.md` (14 events; we catalog all)

## Baseline (already good)

| Area | Grok today | Claude reference |
|---|---|---|
| Event coverage | **14/14** documented | 30 Claude events |
| Implementation | Python `providers/grok/hooks.py` | `providers/claude/hooks.py` |
| Adapter | Thin `adapters/grok.sh` → provider_hook | Thin/hybrid → provider_hook (default) |
| Install | Catalog-driven `install-grok-hooks.sh` | Catalog-driven `install-hooks.sh` |
| Fixtures | 14 JSON under `tests/fixtures/hooks/grok/` | Partial sample set |
| Defaults on | Stop, StopFailure, SubagentStop, Notification, PostToolUseFailure | Similar quiet lifecycle set |
| Dispatch | Smart `hook-notify` + `hook-notify-grok` | `hook-notify` → claude adapter |
| Routing | `project_from_cwd` + RELAY_BACKEND=grok | Same pattern |

## Gaps (why epic #60)

| Gap | Claude leads | Swarm issue |
|---|---|---|
| Install UX | Dry-run plan, explicit no-op, fail-closed malformed, merge narrative | #61 |
| Summary fidelity | Richer templates / field extraction / tests | #62 |
| Test depth | More config override + e2e paths | #63 |
| Matchers | Grok native matcher underused | #64 |
| Operator docs | Claude path better documented | #65 |
| Live smoke | Checklist + metrics | #66 |

## Target quality bar

1. **Install** never leaves broken JSON; second run is silent no-op when unchanged.  
2. **Phone UX** one-line summaries as clear as Claude (emoji prefix + useful detail, no spam).  
3. **Config** `[grok.<Event>]` enabled / prefix / format (+ optional matcher).  
4. **Tests** offline, no live Telegram; all 14 fixtures exercised through format + key adapter paths.  
5. **Docs** new user can wire Grok→Telegram without reading source.

## Non-goals

- PreToolUse **blocking** policy engine (notify-only default)  
- Editing Claude settings from Grok installer  
- Forcing all 14 events default-on (noise)

## Swarm order (recommended)

```
Wave A (parallel): #61 install · #62 format · #65 docs
Wave B (after #62 preferred): #63 fixtures/e2e
Wave C (optional): #64 matchers · #66 smoke
```

Orchestrator merges; `bash scripts/local-ci.sh` before close.

## Resume

```bash
gh issue list --label epic:providers --state open
cat docs/GROK_HOOKS.md docs/WORKFLOW.md
```

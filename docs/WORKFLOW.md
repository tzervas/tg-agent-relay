# Development workflow (orchestrator + swarm)

Canonical process for **tg-agent-relay** and related work.  
Goal: **high velocity + high quality at lower cost** — small, exclusive-file agents do implementation; a thin orchestrator owns joins, board hygiene, and merges.

Related: [EPICS.md](EPICS.md) · [GROK_HOOKS.md](GROK_HOOKS.md) · [AGENT_INTERFACES.md](AGENT_INTERFACES.md) · [TOOLING.md](TOOLING.md) · [RELEASING.md](RELEASING.md)

---

## 1. Roles

| Role | Who | Does | Does **not** |
|---|---|---|---|
| **Orchestrator** | Human or rare flagship session | Epics, issue AC, spawn prompts, merge, local-ci gate, close issues, join-contract freezes | Bulk implementation of S/M tasks |
| **Swarm agent** | Grok Build / smaller code models | **One** GitHub issue, exclusive write set, tests, PR `Fixes #N` | Touch `protocols.py`, invent APIs, expand scope |
| **You (maintainer)** | — | Soak live cutover, approve default flips, repo forks under `tzervas` only | — |

**Default:** decompose → swarm → integrate. Do **not** use a single expensive model to rewrite whole subsystems when children are `swarm-ready`.

---

## 2. Model / cost lanes

Published xAI API list prices (USD / 1M tokens; confirm [docs.x.ai pricing](https://docs.x.ai/developers/pricing)):

| Lane | Model (API) | Input | Output | Use for |
|---|---|---:|---:|---|
| **Build (preferred implementer)** | `grok-build-0.1` | ~$1.00 | ~$2.00 | Scoped coding PRs, S/M/L *implementation* |
| **Flagship (orchestrator sparingly)** | `grok-4.5` | ~$2.00 | ~$6.00 | Hard joins, architecture, conflict resolution only |
| **Fast / mini (if available)** | e.g. 4.1 Fast class | much lower | much lower | Trivial docs, renames, checklist ticks |

**Grok Build CLI** may also be covered by SuperGrok / subscription entitlements (different meter than raw API). Prefer Build for swarms either way.

**Rule of thumb**

- Implementation volume → **Build** (or cheaper).
- Long tool-heavy orchestration on flagship → **expensive**; keep sessions short and issue-bound.
- This chat (flagship + tools) is the **control plane**, not the default implementer.

---

## 3. Board & issue lifecycle

### Labels (required for swarm)

| Label | Meaning |
|---|---|
| `swarm-ready` | Story + AC + API/files + out of scope present |
| `size:S` / `M` / `L` | Effort hint |
| `P0` / `P1` / `P2` | Priority |
| `epic:migration` / `epic:providers` / `epic:product` / `epic:quality` | Parent bucket |
| `python` / `rust` | Language lane |

### States (practical)

1. **Open + swarm-ready** — agent may start  
2. **In progress** — agent comment with branch name  
3. **PR open** — `Fixes #N`  
4. **Integrated** — merged to integration branch, local-ci green  
5. **Closed** — with comment pointing at commit/PR  

Epics stay open until **success criteria** in the epic body are honestly met (not when half the children exist).

### Issue body template (minimum)

Use `.github/ISSUE_TEMPLATE/swarm_task.yml` or paste:

```markdown
## Parent
Epic: #N

## User story
As a … I want … so that …

## Acceptance criteria
- [ ] …

## Write ownership (exclusive)
- path/a
- path/b

## Read-only
- docs/AGENT_INTERFACES.md
- tg_agent_relay/protocols.py

## Tests
```bash
uv run ruff check --fix <owned>
# unit command for this issue
```

## Out of scope
- …

## Done
PR Fixes #N; offline green; no .env secrets
```

---

## 4. Spawn prompt (copy for each agent)

```text
You implement ONLY GitHub issue #N for tzervas/tg-agent-relay.

1. Read: issue body + latest comments; docs/AGENT_INTERFACES.md; docs/TOOLING.md; docs/WORKFLOW.md.
2. Tooling: uv sync --all-groups; Python 3.14; ruff check/format on owned paths only.
3. Multi-except MUST be: except (A, B) as _exc:   # ruff 0.15 format footgun
4. Write ONLY paths under Write ownership. Do not change protocols.py shapes.
5. No live Telegram; no .env edits; no scope expansion.
6. Run the Tests listed on the issue.
7. Commit with conventional message; open PR: “Fixes #N — …”.

Issue:
<paste gh issue view N>
```

**Isolation:** prefer worktree or branch `feat/N-short-slug` off the current integration branch  
(`fix/tts-voice-full-message-v0.5.3` or `main` when merged).

---

## 5. Orchestrator loop (each wave)

```text
┌─────────────────────────────────────────────────────────┐
│ 0. local-ci green on integration tip                    │
│ 1. Pick 3–6 open swarm-ready issues with disjoint files │
│ 2. Post/refresh Agent context comment on each issue     │
│ 3. Spawn agents in parallel (Build lane)                │
│ 4. Wait; collect PRs / branches                         │
│ 5. Merge in order of least conflict                     │
│ 6. Fix except-footgun + ruff; bash scripts/local-ci.sh  │
│ 7. Close issues; update docs/EPICS.md; push             │
│ 8. Comment on parent epic with progress table           │
└─────────────────────────────────────────────────────────┘
```

### File ownership (avoid thrash)

| Area | Typical owner |
|---|---|
| `tg_agent_relay/protocols.py`, `docs/AGENT_INTERFACES.md`, `docs/EPICS.md` | **Orchestrator only** |
| One module + its tests | One agent |
| `tests/run-tests.sh` wiring | Last merge or orchestrator |
| `cli.py` entrypoints | Orchestrator or single agent per wave |

---

## 6. Quality gate (non-negotiable)

**Remote Actions are manual/off.** Workstation is source of truth:

```bash
bash scripts/local-ci.sh              # full gate before merge/push/release
bash scripts/local-ci.sh --release    # before tag
bash scripts/release.sh vX.Y.Z        # publish from this machine
```

Also:

```bash
uv run ruff check --fix <paths>
uv run ruff format <paths>
# Multi-except must keep `as _exc` after format (docs/TOOLING.md)
```

---

## 7. Integration branch & cutover (current product)

| Item | Value |
|---|---|
| Integration branch (as of handoff) | `fix/tts-voice-full-message-v0.5.3` |
| Python ports | Landed (send/poll/format/routing/tts/hooks/…) |
| Live default | **Python** via `tg-send.sh` / `tg-poll.sh` exec (package import) |
| Opt-out shell | `RELAY_PYTHON_SEND=0` · `RELAY_PYTHON_POLL=0` |
| Recovery | `lib/python_fallback.sh` — redacted, sticky, bounded probe (see SETUP) |
| Claude hooks | Prefer `provider_hook` when Python works (`CLAUDE_USE_PROVIDER_HOOK=0` to force shell) |

Details: [RELEASING.md](RELEASING.md) § Python package path.

---

## 7b. Adversarial / performance principles (product code)

When changing runtime paths (hooks, send/poll, config, providers), default to:

1. **Never-silent failure** — recover, but say *why* + how to fix (stderr + metrics). Tests may set `*_QUIET=1`.
2. **Assume hostile env** — `RELAY_PYTHON`, paths, and error strings may contain shell metacharacters or secrets; validate before `exec`, redact before log.
3. **Fail closed on auth, fail open on UX** — allowlist/token mistakes must not send; formatting/TTS/highlight may degrade with a metric.
4. **Dynamic cost under outage** — sticky / TTL / timeouts so a missing package does not fork-probe Python on every hook.
5. **Bounded work** — probe timeouts, TTL caps, reason length caps; no unbounded retries on the hot path.
6. **Private runtime state** — stamp/lock files mode `0600` when possible; never commit `.env`, tokens, sticky stamps.
7. **Same contracts on fallback** — shell recovery must preserve allowlist, routing, format, and TTS semantics.

---

## 8. Open work at handoff (re-check with `gh issue list`)

| # | Title | Lane | Next action |
|---:|---|---|---|
| **18** | Epic: Shell → Python | orchestrator | Live soak with env flags; then decide default cutover issue or close epic |
| **22** | Epic: Rust hotspots | P2 | After #41 |
| **41** | Rust spike benchmarks | Build swarm M | Optional; spawn when wanted |

Providers / product / quality epics (#19–#21) are **closed**. Further provider/ADK/MCP work: open new `swarm-ready` children rather than reopening closed epics unless the epic success criteria truly regress.

---

## 9. Separate universal harness (future repo)

**Not** this repo’s runtime. Planned as **`tzervas/…` only** (e.g. `agent-harness`):

- Extends **tg-agent-relay** (Telegram, providers, MCP server, cutover) as a dependency/integration.
- Composes existing **tzervas** pieces where useful: `agent-mcp`, `mcp-vacuum`, `claude-usage-boilerplate`, `aphelion-agent-security-framework`, `agentic-dev-boilerplate`, mycelium-style CONTRIBUTING.
- **Average Joe’s Labs (AJL)**: read / evaluate only; if useful, **fork to `tzervas`**, work on the fork, PR back with conventional commits. Never push direct to AJL as write base.
- Swarm implementers: **Grok Build** lane; flagship only for architecture ADRs.

Do not land harness core inside `tg-agent-relay` beyond the join surfaces already documented (`providers/`, `mcp_stub`, `extensions`, `adk_bridge`).

---

## 10. Conventional commits (agents + humans)

```
feat(#N): short imperative summary
fix(#N): …
docs(#N): …
test(#N): …
chore: …
```

- One logical change per PR when possible.  
- No force-push to shared branches.  
- No secrets in commits (gitleaks / local-ci).  

---

## 11. Agent “do not” list

- Do not invent parallel protocol types  
- Do not call live Telegram from unit tests  
- Do not edit `.env` or committed tokens  
- Do not expand issue scope “while here”  
- Do not raise MSRV / `requires-python` without orchestrator  
- Do not write multi-except without `as _exc`  

---

## 12. Handoff checklist (before switching models / sessions)

- [ ] `git status` clean or intentional WIP committed  
- [ ] Integration tip pushed  
- [ ] `docs/EPICS.md` matches GitHub open/closed  
- [ ] Open issues have AC + write ownership (or labeled not ready)  
- [ ] Next wave list written in epic comment or here §8  
- [ ] Orchestrator session ends; Build swarms pick up only `swarm-ready` issues  

**Resume command for next orchestrator session:**

```bash
cd /path/to/tg-agent-relay
git checkout fix/tts-voice-full-message-v0.5.3   # or main if merged
git pull
gh issue list --state open
cat docs/EPICS.md docs/WORKFLOW.md
bash scripts/local-ci.sh --quick   # or full before spawning
```

Then: spawn Build agents for the next disjoint `swarm-ready` issues only.

# Development workflow (orchestrator + swarm)

Canonical process for **tg-agent-relay** and related work.  
Goal: **high velocity + high quality at lower cost** — small, exclusive-file agents do implementation; a thin orchestrator owns joins, board hygiene, and merges.

Related: [EPICS.md](EPICS.md) · [DECISIONS.md](DECISIONS.md) · [GROK_HOOKS.md](GROK_HOOKS.md) · [AGENT_INTERFACES.md](AGENT_INTERFACES.md) · [TOOLING.md](TOOLING.md) · [RELEASING.md](RELEASING.md) · [SELF_HOSTED_RUNNER.md](SELF_HOSTED_RUNNER.md) · [gha-runner-ctl](https://github.com/tzervas/gha-runner-ctl)

---

## 0. Branch model

| Branch | Role |
|---|---|
| **`main`** | Default branch. Stable / tags / deploy refs. **Updated only via PR** (never direct push for product work). |
| **`dev`** | **Persistent** integration line. Day-to-day merges land here after review + local-ci. |
| **`feat/N-*` / `fix/*`** | Short-lived. **Cut from `dev`**, PR **into `dev`**. |

```text
  main  ←── PR only (promote / release) ──  dev  ←── PR ──  feat/N-… (off dev)
                                              ▲
                                         persistent
```

**Rules**

1. Working branches always start from current `origin/dev`.
2. Feature PR base is **`dev`**. Do not open new long-lived `fix/tts-…` integration branches.
3. **`main` only changes through a GitHub PR** (typically `dev` → `main`). No “merge locally and push main” as the normal path.
4. After `main` moves, open a small PR or merge-back so **`dev` includes `main`** (keep `dev` ahead or equal, never behind forever).

```bash
git fetch origin
git checkout dev && git pull origin dev
git checkout -b feat/41-rust-spike
# … work …
gh pr create --base dev --title "feat(#41): …" --body "Fixes #41"
bash scripts/merge-pr.sh N    # merges into PR base (usually dev)
```

### Issue and epic close policy

| Kind | When it closes |
|---|---|
| **Task issues** (`[swarm] …`, `Fixes #N` on the feature PR) | Only when that work reaches **`main`** (promote PR carries `Fixes #N` / commit trail, or GitHub auto-close on the main-bound PR). Merging to **`dev` leaves issues open**. |
| **Epics** | Stay open until a **final ship issue** for that epic merges into **`main`** with `Closes #<epic>`. |

```text
  feat PRs → dev     : implement; board issues stay open
  promote PR → main  : Fixes #61 #62 … (tasks) + optional Closes #60 (epic ship issue)
```

**Final epic issue** (one per epic, size S):

```markdown
## Parent
Epic: #60

## User story
As a maintainer, when all children are on main I want the epic closed automatically.

## Acceptance criteria
- [ ] All child issues for this epic are fixed on `dev`
- [ ] Promote PR `dev` → `main` includes `Closes #60` (or this issue is the promote body)
- [ ] No open swarm children remain for the epic

## Write ownership
- docs/EPICS.md (status row only)

## Done
PR into **main** with `Closes #60`.
```

Helpers (main only for closes):

```bash
bash scripts/merge-pr.sh N                      # close only if base=main
bash scripts/close-linked-issues.sh --pr N      # refuses non-main bases
bash scripts/close-linked-issues.sh --pr N --dry-run
```

Self-hosted Actions runner (one shared host): **[tzervas/gha-runner-ctl](https://github.com/tzervas/gha-runner-ctl)** · [SELF_HOSTED_RUNNER.md](SELF_HOSTED_RUNNER.md).

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
2. **In progress** — branch from `dev`, agent comment with branch name  
3. **PR open → `dev`** — body includes `Fixes #N` (for later main auto-close)  
4. **On `dev`** — merged + local-ci green; **issue still open**  
5. **On `main`** — promote PR; task issues close via `Fixes #N`  
6. **Epic closed** — only via final ship issue / `Closes #<epic>` on a **main** PR  

Epics stay open until children are on `main` and the final ship issue closes them.

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
7. Commit with conventional message; open PR body must include `Fixes #N`.

Issue:
<paste gh issue view N>
```

**Isolation:** worktree or `feat/N-short-slug` **off `origin/dev`**, PR base **`dev`**.  
Issue close happens only on **`main`** (see §0).

---

## 5. Orchestrator loop (each wave)

```text
┌─────────────────────────────────────────────────────────┐
│ 0. local-ci green on origin/dev                         │
│ 1. Pick 3–6 open swarm-ready issues with disjoint files │
│ 2. Agent context comment; spawn Build off dev           │
│ 3. PRs base=dev with Fixes #N in body                   │
│ 4. bash scripts/merge-pr.sh N  (into dev; issues open)  │
│ 5. local-ci on dev; update EPICS progress (epic open)   │
│ 6. When shipping: PR dev→main with Fixes #… for tasks   │
│    + final ship issue Closes #<epic> when epic is done  │
│ 7. Tag release from main; keep dev ≥ main               │
└─────────────────────────────────────────────────────────┘
```

| Tool | Role |
|---|---|
| `scripts/merge-pr.sh N` | Merge PR; close issues **only if base is main** |
| `scripts/close-linked-issues.sh --pr N` | Same; **refuses non-main** bases |
| `close-issues-on-merge.yml` | Actions on **main** merges (self-hosted runner) |

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

## 7. Product line (current)

| Item | Value |
|---|---|
| Default branch | **`main`** (stable / tags) |
| Integration branch | **`dev`** (feature PRs merge here) |
| Latest release | **v0.6.1** (see GitHub Releases) |
| Python ports | Landed (send/poll/format/routing/tts/hooks/…) |
| Live default | **Python** via `tg-send.sh` / `tg-poll.sh` exec (package import) |
| Opt-out shell | `RELAY_PYTHON_SEND=0` · `RELAY_PYTHON_POLL=0` |
| Recovery helpers | `lib/python_fallback.sh` (see SETUP / [DECISIONS.md](DECISIONS.md)) |
| Claude hooks | Prefer `provider_hook` when Python works (`CLAUDE_USE_PROVIDER_HOOK=0` to force shell) |

Promote `dev` → `main` when cutting a release or when stable work should land on the default branch. Details: [RELEASING.md](RELEASING.md).

---

## 7b. Docs and design notes

- **User-facing** (README, SETUP): short, practical, humble. Show behavior;
  avoid slogans and internal process language.
- **Code comments / Python docstrings**: Google style. Explain the *why*
  for non-trivial logic; skip restating the obvious.
- **Design decisions** (implemented choices, trade-offs, rejected
  alternatives): [DECISIONS.md](DECISIONS.md).

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
- [ ] `dev` tip pushed (and `main` promoted if a release was cut)  
- [ ] `docs/EPICS.md` matches GitHub open/closed  
- [ ] Open issues have AC + write ownership (or labeled not ready)  
- [ ] Next wave list written in epic comment or here §8  
- [ ] Orchestrator session ends; Build swarms pick up only `swarm-ready` issues  

**Resume command for next orchestrator session:**

```bash
cd /path/to/tg-agent-relay
git fetch origin
git checkout dev && git pull origin dev
gh issue list --state open
cat docs/EPICS.md docs/WORKFLOW.md
bash scripts/local-ci.sh --quick   # or full before spawning
```

Then: spawn Build agents for the next disjoint `swarm-ready` issues only
(branch from `dev`, PR base `dev`).

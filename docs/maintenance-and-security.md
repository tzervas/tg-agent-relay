# Maintenance and validated security scanning

This document describes the workflows added for **weekly maintenance** and
**self-hosted FOSS security scanning with finding validation**. Scope is
`tg-agent-relay` only.

Related (owned by a separate worker): [`docs/branch-sync.md`](branch-sync.md)
covers how `dev` and `sec` stay synced with `main`.

---

## Exit contract (all jobs)

| Situation | Outcome |
|-----------|---------|
| The thing it checks is broken | **FAIL** |
| Work completed successfully | **PASS** |
| There was nothing to do (empty) | **PASS** (exit 0) |
| Could not tell (tool crash, API error, missing auth) | **FAIL** loudly |

**Empty ≠ unknown.** Jobs print which path they took. Collapsing them is how a
gate silently stops gating or how red stops meaning broken.

---

## Runner sizing (measured fleet facts)

Self-hosted podman fleet labels: `runs-on: [self-hosted, linux, x64, podman]` plus
an optional **tier** label.

| Tier | Caps |
|------|------|
| micro | 0.25 cpu / 512 MiB |
| small | 0.5 cpu / 1 GiB |
| medium | 2 cpu / 4 GiB |
| large | 4 cpu / 8 GiB |

**Undersizing is the #1 cause of false CI failures on this fleet.** A compile
measured at 1225 MiB peak against micro’s 512 MiB cap produced SIGKILL 137 and a
~30-minute apparent hang. For Python the equivalent risk is `pip install` /
`uv sync` of a tree that builds native wheels, plus pytest.

An **explicit tier label in `runs-on` takes precedence** over the job-name
heuristic. Without a tier label, names containing `lint`, `ruff`, `fmt`,
`format`, `security`, `gitleaks`, `trivy`, etc. silently force **micro**.

---

## (A) Maintenance — `.github/workflows/maintenance.yml`

Triggers: **weekly** `schedule` (`17 8 * * 1`) + `workflow_dispatch`.

Concurrency: group without `cancel-in-progress` (required for any workflow that
has `schedule:` on self-hosted runners — see cancellation trap below).

### Jobs

| Job name | What it does | Tier | Basis |
|----------|--------------|------|-------|
| `maintenance python style (ruff)` | `uv sync --group dev`, then `ruff check` + `ruff format --check` on `tg_agent_relay`, `providers`, `lib`, `tests` | **medium** | Job name contains `ruff` → heuristic would pin micro (512 MiB). Job **installs the project dev group** via uv → must be ≥ medium. |
| `maintenance dependency age report` | `uv sync --group dev`, then `uv pip list --outdated`; writes step summary | **medium** | Installs locked deps; report-only (outdated packages do **not** fail the gate). Empty outdated list → PASS. |
| `maintenance merged branch report` | `gh api` compare of remote branches vs default; lists candidates already contained in default | **micro** | API + shell only; no install, no compile. **Never deletes** branches. Empty candidate list → PASS. |

Dependency tool: this repo uses **`uv`** + `uv.lock` + `pyproject.toml` (not poetry/pip-tools as primary).

---

## (B) Security validation — `.github/workflows/security-validate.yml`

Triggers: weekly `schedule` (`17 6 * * 1`), `workflow_dispatch`, and pushes to
`sec`.

Self-hosted FOSS only — **no SaaS** (no Snyk, Codecov, CodeQL cloud, or other
vendor phone-home).

### Tools

| Tool | Pin | Why self-hosted FOSS | Job tier | Basis |
|------|-----|----------------------|----------|-------|
| **gitleaks** | **8.30.1** + SHA256 `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb` | Secret scanning without a SaaS broker. Apt on Debian/Ubuntu ships **8.16.0**, a **different incompatible scanner** — must download the pinned release and verify checksum (same pattern as `fleet-security.yml`). | default (**micro**) | Proven at micro by existing fleet-security gitleaks job. |
| **trivy** | **0.72.0** + SHA256 `bbb64b9695866ce4a7a8f5c9592002c5961cab378577fa3f8a040df362b9b2ea` | Filesystem scan: vuln + secret + license; covers Python lockfile/requirements advisories. | default (**micro**) | Proven at micro by existing fleet-security trivy-fs job. |
| **bandit** | via `uvx bandit` | Python SAST, local, no vendor API. | **small** | Parses the whole tree; hungrier than gitleaks; no project `uv sync`. |
| **semgrep** | via `uvx semgrep`, config **`p/python` only** | Cheap, honest SAST pack; no cloud. | **small** | Whole-tree parse; narrow ruleset keeps cost bounded. |
| **pip-audit** | via `uvx pip-audit` after `uv sync --group dev` | Advisory check against the installed graph from this repo’s uv lock. | **medium** | Project dependency install requires ≥ medium. |

### Validation methodology

A wall of unvalidated advisories trains people to ignore red. Every finding is
classified and **never silently dropped**:

| Class | Meaning | Gate |
|-------|---------|------|
| **REACHABLE** | Applicable here: e.g. direct dependency **and** imported; secret hit; SAST construct co-located with attacker/user-input markers | **FAIL** if any |
| **UNREACHABLE** | Not applicable with recorded reason: transitive/dev/optional and not imported; test-only SAST | Listed with reason; does not fail |
| **UNKNOWN** | Could not determine reachability (or tool output missing/unparseable) | **FAIL** if any |

Concretely for Python dependency advisories (trivy / pip-audit):

1. Is the package in `project.dependencies`, an optional extra, a dependency-group, or only transitive in the lock/install graph?
2. Is the package (or its import root) actually imported under `tg_agent_relay/`, `providers/`, `lib/`, etc.?
3. Optional extras (`adk`, `dashboard`, `highlight`) that are not imported in the default tree are **UNREACHABLE** with that reason.

For **bandit**: flag REACHABLE when the construct sits near request/network/user-input markers; test-path hits may be UNREACHABLE; otherwise UNKNOWN (fails).

### Remediation output

Every finding in the report includes a concrete **Remediation** line, for example:

- exact package + version to bump (`uv lock --upgrade-package <name>`)
- secret rotation + history purge guidance
- code/config change for SAST hits

A finding with no stated fix is not considered done.

### Report outputs

1. **Job summary** (`$GITHUB_STEP_SUMMARY`) — human-readable markdown
2. **Artifact** `validated-security-report` — markdown + raw JSON from each tool
3. **`sec` branch** — `docs/security-reports/latest.md`, timestamped copy, and `docs/security-reports/raw/*.json`

Publishing commits **only** to `sec`, never to `main` or `dev`.

---

## (B continued) The `sec` branch

### Why

Security scan **output** needs a home that does not block feature flow on `dev`
or release flow on `main`. Validated reports land on `sec`.

### Creation

This repo had no `sec` branch. It was created **non-destructively** from the
current default-branch tip:

```bash
git push origin origin/main:refs/heads/sec
```

That only creates a new ref; it destroys nothing. Confirm with
`git ls-remote origin refs/heads/sec` (SHA recorded in the introducing PR/report).

### Sync policy

`sec` is kept in sync with `main` by the **same non-destructive merge discipline
used for `dev`**:

- **Merge from `main` into `sec`**
- **Never** force-push
- **Never** reset

A separate worker owns `.github/workflows/branch-sync.yml` and
[`docs/branch-sync.md`](branch-sync.md). Do not edit those files from the
maintenance/security lane.

---

## (C) Scheduled-workflow cancellation trap

**Rule:** never put `cancel-in-progress: true` on a workflow that has a
`schedule:` trigger and runs on self-hosted runners. A scheduled job queues with
no runner available; the next schedule cancels it; it reports `cancelled` rather
than `failed` — so it silently never runs and nothing alerts.

### Fixes / inspection

| File | Finding | Action |
|------|---------|--------|
| `.github/workflows/fleet-security.yml` | Had `schedule:` **and** `cancel-in-progress: true` | Set `cancel-in-progress: false` (only change) |
| `.github/workflows/gitleaks.yml` | `workflow_dispatch` only; no `schedule:`; no concurrency cancel trap | **No change** |

New workflows (`maintenance.yml`, `security-validate.yml`) use concurrency groups
with `cancel-in-progress: false`.

---

## Files owned by this work

| Path | Role |
|------|------|
| `.github/workflows/maintenance.yml` | Weekly maintenance jobs |
| `.github/workflows/security-validate.yml` | Validated FOSS security scan → `sec` |
| `.github/workflows/fleet-security.yml` | Cancel-in-progress fix only |
| `docs/maintenance-and-security.md` | This document |

Not owned here: `README.md`, `docs/branch-sync.md`,
`.github/workflows/branch-sync.yml`.

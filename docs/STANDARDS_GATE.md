# The development-standards gate

`.github/workflows/standards.yml` is a thin caller for
`tzervas/mycelium-workflows/.github/workflows/reusable-standards.yml`. All policy lives
centrally; this repo carries only its overrides. The full rule reference, the operator
commands and the known limits are in
[`mycelium-workflows/docs/STANDARDS.md`](https://github.com/tzervas/mycelium-workflows/blob/main/docs/STANDARDS.md).

This page covers what is specific to **tg-agent-relay**.

## What it reports

One status context: **`standards / standards`**.

It is `standards / standards` and not `standards` because a reusable workflow prefixes the job
name with the caller's job id, and **the prefix cannot be suppressed**. A ruleset that requires
the bare `standards` would require a context that never reports — the PR would not go red, it
would wait forever with nothing to diagnose.

**Do not add it to a ruleset until this caller has landed and reported once.** Confirm the exact
spelling first:

```bash
gh pr checks <n> --repo tzervas/tg-agent-relay
```

## What the gate found here on day one

Two warnings, no errors. Both are real and both are recorded here rather than silenced.

### 1. `dev` is far behind `main` — the squashed-promote residue

Measured at adoption:

| measurement | value |
|---|---|
| `main` / `dev` merge base | `a140d60a` |
| `main` commits not in `dev` | 19 |
| `dev` commits not in `main` | 1 |
| diff `dev` → `main` | 67 files, 5,865 insertions |

That 5,865-line number is not abstract: it is what every branch cut from `dev` will be asked to
resolve as conflict when it is retargeted, even when the work was already merged. It is the same
damage the contract records against this repo (`dev` 17 commits / ~5,700 lines behind, with zero
unique content), now measured independently by the gate.

The fix is a back-merge, **as a merge commit** — never a reset and never a force-push:

```bash
gh pr create --base dev --head main \
  --title 'chore(sync): back-merge main into dev — restore the merge base'
gh pr merge <n> --merge
```

That is deliberately **not** done in this PR: it is a separate, reviewable change to `dev`'s
history and does not belong in the PR that installs the gate.

### 2. Version drift — two sources, two answers

| source | version |
|---|---|
| `VERSION` | `0.8.1` |
| `pyproject.toml` | `0.7.0.dev0` |

Contract §4 requires correct semver and a changelog derived from it, which is impossible while
the number itself is ambiguous. This repo also has **no `.cz.toml`**, so nothing keeps the two in
lockstep automatically.

The rule is at `warn`, so this does not block. It is left for a change that owns versioning
rather than being bundled into a CI adoption — adding a third source right now would make the
drift worse, not better. The shape of the fix:

```toml
# .cz.toml
[tool.commitizen]
name = "cz_conventional_commits"
version = "0.8.1"
tag_format = "v$version"
version_scheme = "semver"
major_version_zero = true
version_files = ["VERSION", "pyproject.toml:^version"]
```

Then `cz bump` rewrites both together and there is one answer.

## Modes in force here

Everything is at the central default — nothing is downgraded, so any rule that goes red here is
a real finding rather than a policy exception:

| rule | mode |
|---|---|
| `promote-merge-mode`, `branch-targeting`, `protected-refs`, `trunk-divergence` | enforce |
| `version-policy`, `yaml-validity`, `schedule-cancel`, `conventional-title` | enforce |
| `version-drift`, `exit-contract`, `python-floor`, `docs-with-change` | warn |
| `actionlint` | off |

## Runner sizing

The caller passes no size label. Verified 2026-07-25, the fleet's registered runner advertises
`[self-hosted, Linux, X64, podman]` and nothing else, so requiring `small` would queue the job
against a label nothing serves. `gha-runner-ctl`'s `size_for_job` checks labels before the name
heuristic; with no label the job name `standards` lands on the Medium catch-all (2 CPU / 4 GiB),
which is ample for a Python linter. Add the label to `runner-labels` once workers register it.

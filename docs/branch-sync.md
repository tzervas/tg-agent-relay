# Branch sync — automated non-destructive back-merge

How `main` is reconciled into the lower branches `dev` and `sec` after every
promote, without ever rewriting history.

## Root cause (measured)

Promote PRs from `dev` into `main` have been **squash-merged**. Squashing gives
`main` a single new commit instead of `dev`'s history, so the two branches
**diverge in ancestry** even when their tree content mostly agrees.

Measured via the GitHub compare API (`dev...main`) at the time this automation
was added:

| signal | value |
|---|---|
| compare `status` | `diverged` |
| `main` ahead of `dev` | **17 commits** across **69 files** (~5,700 lines) |
| `dev` ahead of `main` | **exactly 1 commit** |

That one commit unique to `dev` is:

```text
docs(P22d): tgar-rs strangler pointer and ROADMAP link
```

touching only `ROADMAP.md` and `docs/TGAR_RS.md`. It is the concrete reason
reconciliation **must be a merge**: a `reset --hard` or force-push of `dev`
onto `main` would **silently destroy** it. The release docs already required a
back-merge step (`docs/RELEASING.md` step 4); that step was skipped for multiple
releases, which is what produced the drift.

The one-off fix for the measured divergence is **PR #115**
(`chore(sync): re-baseline dev onto main (0.10.2)`, head `main` → base `dev`).
This document and workflow are the **recurrence prevention**, not a substitute
for that PR.

## Recommendation (documentation only)

> **Promote PRs from `dev` into `main` must be merged with a MERGE COMMIT, not
> SQUASH.**

Squash is the root cause of ancestry drift. This repo's settings are **not**
changed by this automation — operators should prefer "Create a merge commit"
when merging `dev` → `main` promote PRs. (Agents must not edit rulesets or
branch-protection settings.)

## What the workflow does

Workflow file: [`.github/workflows/branch-sync.yml`](../.github/workflows/branch-sync.yml)

| trigger | purpose |
|---|---|
| `push` to `main` | after every merge into `main`, back-merge into lowers |
| `workflow_dispatch` | manual re-run |
| `schedule` (daily) | safety net if a push run was missed |

For **each** downstream branch in `{dev, sec}`:

1. **Missing branch** — create it from `main`, report created → **PASS**.
2. **Already contains `main`** (`main` is an ancestor) — print clearly, open
   **no** PR → **PASS** ("nothing to do" is success).
3. **Needs update** — `git merge --no-ff origin/main` on a work branch:
   - **Clean merge** — push
     `chore/sync-main-into-<branch>-<short-sha>` and **open or update** a PR
     into the downstream branch. The workflow does **not** merge the PR.
   - **Conflict or tool/API failure** — **FAIL loudly** with the branch name,
     conflicting paths, and exact local commands for a human.

### Guarantee: merge only, never destroy

| allowed | forbidden |
|---|---|
| `git merge --no-ff origin/main` | `git push --force` / `--force-with-lease` to `dev`/`sec` |
| create missing `sec` from `main` | `git reset` of a protected lower onto `main` |
| open/update a sync PR | rebase of `dev` / `sec` onto `main` |

Reconciliation is **always a merge**, so commits that exist only on the lower
(e.g. `dev`'s P22d docs commit above) are preserved in history. A reset or
force-push of `dev` onto `main` would have destroyed that commit.

### PR update behaviour

Sync PRs use the title prefix `chore(sync): merge main into <branch>`. If an
open PR with that prefix already targets the same base, the job updates that
PR's head (push of new merge commits, no force) and body instead of opening a
second PR. Unrelated PRs (including #115, whose head is `main` itself) are
left alone.

## Exit contract

Same contract as the fleet-wide branch/release rules ("red must mean broken"):

| situation | outcome |
|---|---|
| the thing it checks is broken (merge conflict on the lower) | **FAIL** |
| the work was done successfully (created branch, opened/updated PR) | **PASS** |
| there was nothing to do (already up to date with `main`) | **PASS** (exit 0) |
| it could not tell (API/auth/git/`gh` error, non-FF update of existing PR head) | **FAIL loudly** |

"Empty" and "unknown" are different code paths. An up-to-date branch is empty
work and exits 0. A failed `gh pr list` is unknown and exits non-zero.

## Runner tier and basis

| | |
|---|---|
| `runs-on` | `[self-hosted, linux, x64, podman, small]` |
| tier caps | **small** = 0.5 cpu / 1 GiB |
| why not micro | micro is 0.25 cpu / 512 MiB and is for tiny scanners; full-history checkout + `git` + `gh` needs headroom |
| why not medium+ | this job **compiles nothing** — only git plumbing and GitHub API calls |
| explicit label | the `small` label **wins** over job-name heuristics in `gha-runner-ctl` |

No `cancel-in-progress` on the concurrency group: this workflow has a
`schedule` trigger on self-hosted runners; cancelling a queued scheduled run
reports `cancelled` (not `failed`) and silently never runs.

Permissions are minimum viable: `contents: write` (push sync branches / create
missing lowers) and `pull-requests: write` (open/edit sync PRs).

`actions/checkout` uses `fetch-depth: 0` (full history required for a real
merge) and `persist-credentials: true` so subsequent `git push` uses the
job token without rewriting `origin`.

## Manual bootstrap (automation unavailable)

```bash
git fetch origin

# --- dev: non-destructive merge of main ---
git checkout dev
git pull origin dev
git merge --no-ff origin/main -m "chore(sync): merge main into dev"
# if conflicts: resolve, git add -A, git commit
git push origin dev
# or, if dev is protected, push a work branch and open a PR:
#   git push -u origin HEAD:chore/sync-main-into-dev-manual
#   gh pr create --base dev --head chore/sync-main-into-dev-manual \
#     --title "chore(sync): merge main into dev"

# --- sec: create if missing, else same merge ---
if git rev-parse --verify origin/sec >/dev/null 2>&1; then
  git checkout sec
  git pull origin sec
  git merge --no-ff origin/main -m "chore(sync): merge main into sec"
  git push origin sec
else
  git push origin origin/main:refs/heads/sec
fi
```

Never:

```bash
# DESTROYS unique commits on dev (e.g. the P22d docs commit)
git checkout dev
git reset --hard origin/main
git push --force origin dev
```

## Related

- One-off re-baseline while automation lands: **PR #115**
- Release / promote flow (includes the manual back-merge step this workflow
  automates): [`docs/RELEASING.md`](RELEASING.md)
- Fleet branch contract: promote is one direction; lowers re-sync off `main`

# Self-hosted GitHub Actions runner (Podman)

Local workstation runner so Actions jobs (issue close on `main`, optional
`local-ci` via `workflow_dispatch`) stay fast and do not depend on GitHub-hosted
minutes. Feature work still merges on **`dev`**; only **`main`** merges close
issues.

## Resources

Default allocation (override with env):

| Resource | Default | Notes |
|---|---|---|
| CPUs | **5** | ~4–6 is enough for ruff + offline tests |
| Memory | **8g** | Hard cap (`--memory-swap` same) |
| PIDs | 4096 | Avoid runaway forks |

Host currently has spare headroom; leave most cores for interactive work.

## One-time setup

```bash
# Podman required
command -v podman

# Registration token (expires ~1 hour; only needed if no persisted config)
export RUNNER_TOKEN="$(
  gh api -X POST repos/tzervas/tg-agent-relay/actions/runners/registration-token --jq .token
)"

bash scripts/self-hosted-runner/run-runner.sh start
```

Optional overrides:

```bash
RUNNER_CPUS=6 RUNNER_MEMORY=10g \
  RUNNER_NAME=tg-relay-podman-1 \
  bash scripts/self-hosted-runner/run-runner.sh start
```

## Day-to-day

```bash
bash scripts/self-hosted-runner/run-runner.sh status
bash scripts/self-hosted-runner/run-runner.sh logs
bash scripts/self-hosted-runner/run-runner.sh stop
```

Confirm in GitHub: **Settings → Actions → Runners** — labels should include
`self-hosted`, `linux`, `x64`, `podman`.

## Workflows

| Workflow | `runs-on` | When |
|---|---|---|
| `close-issues-on-merge.yml` | `[self-hosted, linux, x64]` | PR merged into **main** only |
| `ci.yml` / others | still `ubuntu-latest` + `workflow_dispatch` unless you retarget | optional later |

If no self-hosted runner is online, main-merge close jobs will queue until one
appears (or you re-run after `run-runner.sh start`). Local fallback:

```bash
bash scripts/close-linked-issues.sh --pr N   # only closes when PR base is main
```

## Security notes

- Do not commit `RUNNER_TOKEN` or registration tokens.
- Prefer repo-scoped registration tokens over org-wide when possible.
- Runner executes workflow code with access to `GITHUB_TOKEN` for that job;
  keep workflows minimal (this repo’s close job only runs the close script).
- Stop the container when the workstation is untrusted or offline for long periods.

## Image rebuild

```bash
bash scripts/self-hosted-runner/run-runner.sh build
bash scripts/self-hosted-runner/run-runner.sh stop
bash scripts/self-hosted-runner/run-runner.sh start
```

Bump `RUNNER_VERSION` in `Containerfile` when GitHub requires a newer runner.

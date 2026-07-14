# One self-hosted runner (Podman + `gha-runner-ctl`)

Exactly **one** GitHub Actions runner for this repo. A small Rust controller
listens for demand, registers with a short-lived token, and spins the container
up/down. A **snapshot baseline** (image + seeded volume) keeps startup near
zero — no runner download on the hot path.

## Pieces

| Piece | Role |
|---|---|
| `scripts/self-hosted-runner/Containerfile` | Image with runner binary + deps |
| Snapshot volume `tg-agent-relay-gha-runner-data` | Pre-seeded runner home |
| `gha-runner-ctl` | Listen / prepare / up / down (one container only) |

## Modes

| Mode | Behavior |
|---|---|
| **`ephemeral`** (default) | Fresh registration each `up` (`config.sh --ephemeral`). Runner leaves GitHub after one job. Best for security. |
| **`retain`** | Keep `.runner` on the volume; start/stop only. Use when you want the same registration across restarts. |

## Auth (secure)

Controller obtains a **registration token** (≈1h, single-use style) via:

1. `GH_TOKEN` / `GITHUB_TOKEN`, or  
2. `gh auth token` (preferred on this workstation)

Fine-grained PAT needs **Administration: Read and write** on this repository
(or classic `repo` admin). The short-lived registration token is written to a
`0600` env-file, passed into Podman, then deleted — never printed.

## Relation to GitHub’s “New self-hosted runner” UI

GitHub shows the classic install steps (download tarball → `./config.sh` →
`./run.sh`). We automate the same flow for **one** runner:

| UI step | Our equivalent |
|---|---|
| Download `actions-runner-linux-x64-2.335.1.tar.gz` + sha256 | Baked into the Podman image (`Containerfile`, hash-checked) |
| `./config.sh --url … --token …` | `gha-runner-ctl up` fetches a **short-lived** registration token via API and injects it (never commit UI tokens) |
| `./run.sh` | Container entrypoint |
| `runs-on: self-hosted` | Workflows use `[self-hosted, linux, x64]` |

Do **not** paste one-off UI tokens into git, scripts, or chat. If a token was
exposed, ignore it — the controller mints a new one each `up`.

## Quick start

```bash
# Build controller
cargo build -p gha_runner_ctl --release
export PATH="$PWD/target/release:$PATH"

# Auth: gh login (or GH_TOKEN with Administration: read/write on this repo)
gh auth status

# Once: image + volume snapshot (warm baseline, runner 2.335.1)
gha-runner-ctl prepare

# On demand (manual) — auto-registers, no UI token
gha-runner-ctl up
gha-runner-ctl status
gha-runner-ctl down

# Or: listen for queued self-hosted jobs / idle timeout
gha-runner-ctl listen --interval 15 --idle-secs 120
# Optional local poke (loopback only):
# gha-runner-ctl listen --wake-port 7099
# curl -X POST http://127.0.0.1:7099/wake
```

Resources default to **5 CPUs / 8 GiB** (`--cpus` / `--memory` or `GHA_CPUS` /
`GHA_MEMORY`).

## Why snapshot

| Step | Cold | After `prepare` |
|---|---|---|
| Pull/build image | slow | local image |
| Download runner tarball | slow | already on volume |
| `config.sh` + `run.sh` | seconds | seconds (register only) |
| `podman start` retained | — | sub-second process start |

`prepare` copies runner binaries into the volume and stamps
`.snapshot-baseline`. Ephemeral mode still re-registers (required for a clean
one-shot runner) but does **not** re-download tooling.

## Listen loop

```
poll GitHub for queued/in_progress jobs with labels self-hosted|podman
  → demand & not running  →  up
  → no demand & running & idle ≥ idle-secs  →  down
```

Only **one** container name is used (`gha-runner-tg-agent-relay`); a second
instance is not started.

## Workflows

`close-issues-on-merge.yml` uses `runs-on: [self-hosted, linux, x64]` and only
on **main** merges. Start `gha-runner-ctl listen` (or `up`) on the workstation
when you expect a promote-to-main PR.

## Not in scope

- Multi-runner pools, autoscaling fleets, K8s ARC  
- Org-wide runners  
- Long-lived registration tokens in git or world-readable files  

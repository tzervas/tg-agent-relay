# Self-hosted runner (shared)

This repo does **not** vendor a runner controller. Use the standalone MIT project:

**[tzervas/gha-runner-ctl](https://github.com/tzervas/gha-runner-ctl)** · **[v0.2.0](https://github.com/tzervas/gha-runner-ctl/releases/tag/v0.2.0)**

One Podman runner on the workstation. GitHub queues jobs; you do not run one instance per repo.

## The runner is rootless — `sudo` never works in a job

The Podman runner runs rootless with `no_new_privs` set, so any job step that
shells out to `sudo` fails immediately and unconditionally:

```
sudo: The "no new privileges" flag is set, which prevents sudo from running as root.
```

This is the container doing its job, not a misconfiguration. It means a workflow
**cannot** `apt-get install` a missing tool on this runner, whatever the
packaging.

**Preferred fix: bake the tool into the runner base image.** Workflows here
already guard with `command -v <tool> || install`, so once the image ships the
tool the guard short-circuits and the install path never runs. `gh` is the one
that has bitten so far — `close-issues-on-{main,merge}.yml` both need it.

Until an image ships it, those workflows fall back to installing the release
tarball into `$RUNNER_TEMP/bin` and appending that to `$GITHUB_PATH`:
checksum-verified, writes nothing outside the job's own temp dir, and needs no
privilege at all. That fallback becomes dead code the moment the image carries
the tool — the two fixes do not conflict.

When adding a step that needs a new binary, prefer in this order:

1. it is already in the image
2. a `uses:` action that vendors its own binary
3. a checksum-verified release tarball into `$RUNNER_TEMP/bin`
4. never `sudo`

## Install from release (no cargo; works while Actions runner is down)

```bash
VER=0.2.0
TARGET=x86_64-unknown-linux-gnu
BASE="https://github.com/tzervas/gha-runner-ctl/releases/download/v${VER}"

curl -fsSL -o "gha-runner-ctl-${VER}-${TARGET}.tar.gz" \
  "${BASE}/gha-runner-ctl-${VER}-${TARGET}.tar.gz"
curl -fsSL -o "SHA256SUMS-${VER}.txt" \
  "${BASE}/SHA256SUMS-${VER}.txt"
sha256sum -c "SHA256SUMS-${VER}.txt"
tar xzf "gha-runner-ctl-${VER}-${TARGET}.tar.gz"
cd "gha-runner-ctl-${VER}-${TARGET}"
bash install.sh
export PATH="$HOME/.local/bin:$PATH"
gha-runner-ctl prepare
```

## Listen modes

```bash
# This checkout only (auto owner/repo from git / gh)
cd /path/to/tg-agent-relay
gha-runner-ctl --scope repo --auto listen --interval 30 --idle-secs 180

# Batch all personal tzervas repos (one process; re-registers per demand)
gha-runner-ctl --scope user --user tzervas listen --interval 30 --idle-secs 180

# Org (repos must live under that org — not personal tzervas/* outside it)
# gha-runner-ctl --scope org --owner vectorweighttechnologies listen --interval 30 --idle-secs 180
```

## This repo’s workflows

```yaml
runs-on: [self-hosted, linux, x64, podman]
```

See `.github/workflows/close-issues-on-merge.yml` and
[gha-runner-ctl docs/CONSUMERS.md](https://github.com/tzervas/gha-runner-ctl/blob/main/docs/CONSUMERS.md).

## License

- `tg-agent-relay`: MIT  
- `gha-runner-ctl`: MIT (cites GitHub’s `actions/runner`, also MIT)  

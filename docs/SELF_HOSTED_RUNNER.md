# Self-hosted runner (shared)

This repo does **not** vendor a runner controller. Use the standalone MIT project:

**[tzervas/gha-runner-ctl](https://github.com/tzervas/gha-runner-ctl)** · **[v0.2.0](https://github.com/tzervas/gha-runner-ctl/releases/tag/v0.2.0)**

One Podman runner on the workstation. GitHub queues jobs; you do not run one instance per repo.

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

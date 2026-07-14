# Self-hosted runner (shared)

This repo does **not** vendor a runner controller. Use the standalone MIT project:

**[tzervas/gha-runner-ctl](https://github.com/tzervas/gha-runner-ctl)** · [v0.1.0](https://github.com/tzervas/gha-runner-ctl/releases/tag/v0.1.0)

One Podman runner on the workstation; every consumer repo uses the same labels.
GitHub queues jobs and dispatches them — you do not run one instance per repo.

## Host (once)

```bash
git clone https://github.com/tzervas/gha-runner-ctl.git
cd gha-runner-ctl
bash packaging/install-ctl.sh
export PATH="$HOME/.local/bin:$PATH"

gha-runner-ctl prepare

# Single-repo (personal account)
GHA_SCOPE=repo GHA_REPO=tzervas/tg-agent-relay \
  gha-runner-ctl listen --interval 30 --idle-secs 180

# Shared across an organization (recommended for many repos)
# GHA_SCOPE=org GHA_OWNER=your-org gha-runner-ctl listen --interval 30 --idle-secs 180
```

## This repo’s workflows

Use labels that match the host:

```yaml
runs-on: [self-hosted, linux, x64, podman]
```

See `.github/workflows/close-issues-on-merge.yml` and [gha-runner-ctl docs/CONSUMERS.md](https://github.com/tzervas/gha-runner-ctl/blob/main/docs/CONSUMERS.md).

## License

- `tg-agent-relay`: MIT  
- `gha-runner-ctl`: MIT (cites GitHub’s `actions/runner`, also MIT)  

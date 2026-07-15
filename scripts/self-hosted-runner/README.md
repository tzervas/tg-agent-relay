# Moved

Runner tooling lives in **[tzervas/gha-runner-ctl](https://github.com/tzervas/gha-runner-ctl)**.
Prefer a **release tarball** install (no cargo; works when Actions is down).

```bash
# Preferred: release install (see docs for VER / TARGET / checksums)
# https://github.com/tzervas/gha-runner-ctl/releases — e.g. v0.2.0
# Full steps: docs/SELF_HOSTED_RUNNER.md

# Optional: clone + packaging install (dev of the controller itself)
git clone https://github.com/tzervas/gha-runner-ctl.git
cd gha-runner-ctl && bash packaging/install-ctl.sh
```

See [docs/SELF_HOSTED_RUNNER.md](../../docs/SELF_HOSTED_RUNNER.md) for release install, `--auto` / batch listen, and labels.

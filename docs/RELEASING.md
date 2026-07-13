# Releasing TG Agent Relay

## Version policy

| Kind | Example | When |
|---|---|---|
| **dev** | `0.6.0-dev` | Default on `main` / feature branches (`VERSION` file) |
| **patch** | `v0.5.3` | Fixes only (TTS, docs, small bugs) |
| **minor** | `v0.6.0` | Features (providers, project rooms, Python ports) |
| **major** | `v1.0.0` | Breaking defaults or public API changes |

- Git tags are always `vMAJOR.MINOR.PATCH` (optional `-rc.N`).
- `VERSION` file holds the **next** in-progress version (`X.Y.Z-dev` until cut).
- **Python: 3.14 preferred** (see `lib/python.sh`).

## When to cut a release

Ready when:

1. Offline tests green (`bash tests/run-tests.sh` / future `pytest`).
2. Release notes drafted (user-facing changes only).
3. No secrets in tree (gitleaks clean).
4. Local smoke optional: one hook ping + `/help` after deploy.

Epic board: [docs/EPICS.md](EPICS.md). Do **not** wait for full shell→Python (#18) to ship useful minors.

## Cut a GitHub release

From a clean tree on the release commit:

```bash
# 1. Set VERSION to the release number (no -dev)
echo "0.6.0" > VERSION

# 2. Commit + tag + publish (script does checks)
bash scripts/release.sh v0.6.0

# Or dry-run:
bash scripts/release.sh v0.6.0 --dry-run
```

What `scripts/release.sh` does:

1. Verifies `VERSION` matches the tag (without `v`).
2. Runs offline tests (unless `--skip-tests`).
3. Creates annotated git tag `vX.Y.Z`.
4. Pushes tag to `origin`.
5. Creates GitHub Release with auto notes + optional `RELEASE_NOTES.md` body.
6. Attaches a source tarball (optional).

After publish, bump to next dev:

```bash
echo "0.6.1-dev" > VERSION
git add VERSION && git commit -m "chore: bump VERSION to 0.6.1-dev"
```

## Update the local deployed instance

Production path (deliberate): **`~/.claude/telegram-bridge/`**  
(repo may live at `~/work/tg-agent-relay` or elsewhere).

```bash
# Deploy current working tree (after merge/tag) into the live bridge:
bash scripts/deploy-local.sh

# Deploy a specific release tag:
bash scripts/deploy-local.sh --ref v0.6.0

# Dry-run (show rsync plan only):
bash scripts/deploy-local.sh --dry-run
```

### Preserved on deploy (never overwritten)

| Path | Why |
|---|---|
| `.env` | Bot token + allowlist |
| `relay.toml` | Local config |
| `.offset` / `.tg-buffer*` | Poll state |
| `.metrics.log` | Metrics history |
| `.usage/` | Usage cache |
| `.chats.d/` | Project-room binds |
| `voices/` | Piper models |
| `.tg-send.lock` | Runtime lock |

### Updated on deploy

Code and docs: `*.sh`, `lib/`, `adapters/`, `handlers/`, `providers/`, `docs/`, `tests/`, examples, `VERSION`, etc.

### After deploy

```bash
# Re-sync hooks if installers changed
bash ~/.claude/telegram-bridge/install-hooks.sh --dry-run
bash ~/.claude/telegram-bridge/install-grok-hooks.sh --dry-run
# Apply if the plan looks right:
# bash ~/.claude/telegram-bridge/install-hooks.sh
# bash ~/.claude/telegram-bridge/install-grok-hooks.sh

# Restart anything that long-polls (your Monitor / systemd unit / tmux)
```

## CI (tag releases)

Pushing a `v*` tag runs `.github/workflows/release.yml` (if enabled):

- Checkout tag
- Run offline tests on Python 3.14 when available
- Create/refresh GitHub Release assets

Manual releases via `scripts/release.sh` remain the primary path.

## Suggested first post-dev release

**`v0.6.0`** when Wave 1–2 of the Python migration is partly landed *or* when you want to ship current main features:

- Grok full provider + provider registry
- Project rooms + multi-backend routing  
- Voice spoken_mode + collapse refs  
- Hybrid context_select  
- Python 3.14 resolver  

Until then keep `VERSION=0.6.0-dev` and deploy from git with `deploy-local.sh` as needed.

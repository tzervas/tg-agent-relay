# Releasing TG Agent Relay

## Local-first policy

**This workstation is the source of truth for quality gates and releases.**

### Python package path (default — epic #18 / #67)

**Python is the default** for send and poll when `tg_agent_relay` imports cleanly.
Shell implementations remain as fallback and explicit opt-out:

```bash
# Default (no env needed): tg-send.sh / tg-poll.sh → Python modules
bash tg-send.sh "hello"
bash tg-poll.sh

# Force legacy shell (debug / bisect):
export RELAY_PYTHON_SEND=0
export RELAY_PYTHON_POLL=0
# On unexpected import failure, shell still runs; stderr + metrics note it.
# Optional: RELAY_PYTHON_FALLBACK_TTL, RELAY_PYTHON_PROBE_TIMEOUT
# (see SETUP.md, docs/DECISIONS.md).

# Or call entry points directly:
uv run tg-relay-send "hello"
uv run tg-relay-poll

# Claude hooks: provider_hook preferred when Python works.
# Force shell formatting: CLAUDE_USE_PROVIDER_HOOK=0
```

**Deploy checklist**

1. `bash scripts/local-ci.sh` green on the deploy commit.
2. Deploy: `bash scripts/deploy-local.sh` (preserves `.env` / `relay.toml`).
3. Ensure deploy tree includes `tg_agent_relay/` + `providers/` + `lib/python_fallback.sh`
   and a working Python 3.14 (uv `.venv` or `lib/python.sh` resolution).
4. Smoke: one hook ping + one Telegram inbound message.
5. Confirm code docs when `[code_highlight] mode = "html-doc"`.
6. Only set `RELAY_PYTHON_SEND=0` / `RELAY_PYTHON_POLL=0` if you must bisect.

| Step | Where | Command |
|---|---|---|
| Lint + format + tests + MSRV | **Local** | `bash scripts/local-ci.sh` |
| Cut release (tag + GitHub Release + tarball) | **Local** | `bash scripts/release.sh vX.Y.Z` |
| Deploy live bridge | **Local** | `bash scripts/deploy-local.sh --ref vX.Y.Z` |
| GitHub Actions `ci` / `release` / `gitleaks` | **Manual only** | Actions → workflow_dispatch (optional) |

Remote CI is **not** required for day-to-day development or for publishing a release.
Pushing a tag does **not** auto-run release jobs (avoids the v0.6.0 remote flakiness).

---

## Version policy

| Kind | Example | When |
|---|---|---|
| **dev** | `0.6.1-dev` | Default on feature branches (`VERSION` file) |
| **patch** | `v0.5.3` | Fixes only (TTS, docs, small bugs) |
| **minor** | `v0.6.0` | Features (providers, project rooms, Python ports) |
| **major** | `v1.0.0` | Breaking defaults or public API changes |

- Git tags are always `vMAJOR.MINOR.PATCH` (optional `-rc.N`).
- `VERSION` file holds the **next** in-progress version (`X.Y.Z-dev` until cut).
- **Python: 3.14 preferred** (see `lib/python.sh` / uv).

---

## When to cut a release

Ready when **all** of these pass **locally**:

1. `bash scripts/local-ci.sh --release` exits 0  
   (uv sync, ruff check + format --check, rust-check / MSRV 1.96, full `tests/run-tests.sh`, package CLI smoke)
2. Working tree clean; `VERSION` matches the release base (or script will commit the non-`-dev` bump).
3. Release notes drafted if non-trivial (`--notes-file`).
4. Optional: `bash scripts/local-ci.sh --release --with-gitleaks` if `gitleaks` is installed.
5. Optional smoke: one hook ping + `/help` after deploy.

Epic board: [docs/EPICS.md](EPICS.md). Do **not** wait for full shell→Python (#18) to ship useful minors.

---

## Cut a GitHub release (local only)

From a clean tree on the release commit:

```bash
# 0. Full local gate (required mental model — release.sh runs it again)
bash scripts/local-ci.sh --release

# 1. Set VERSION to the release number (no -dev) if still -dev
echo "0.6.1" > VERSION
git add VERSION && git commit -m "chore(release): prepare 0.6.1"

# 2. Tag + push + publish GitHub Release via gh (re-runs local-ci)
bash scripts/release.sh v0.6.1

# Dry-run (gate + notes preview, no tag/push):
bash scripts/release.sh v0.6.1 --dry-run
```

What `scripts/release.sh` does:

1. Verifies clean tree and `VERSION` matches tag (without `v`).
2. Runs **`scripts/local-ci.sh --release`** (unless `--skip-tests`).
3. Creates annotated git tag `vX.Y.Z`.
4. Pushes branch + tag to `origin`.
5. Builds source tarball with `git archive`.
6. Creates (or refreshes assets on) GitHub Release with **`gh release create`**.

After publish, bump to next dev:

```bash
echo "0.6.2-dev" > VERSION
git add VERSION && git commit -m "chore: bump VERSION to 0.6.2-dev"
git push origin HEAD
```

### Re-running / fixing a botched remote attempt

If an old tag-triggered Actions run failed but the release already exists (e.g. v0.6.0
was created by `release.sh` while remote `release.yml` failed shellcheck):

- Prefer **not** re-tagging; fix on a new patch if needed.
- Optional asset refresh: Actions → **release** → Run workflow → enter existing tag
  (manual only; still prefer regenerating the tarball with local `git archive` +
  `gh release upload TAG archive.tar.gz --clobber`).

---

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

Code and docs: `*.sh`, `lib/`, `adapters/`, `handlers/`, `providers/`, `tg_agent_relay/`,
`docs/`, `tests/`, examples, `VERSION`, etc.

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

---

## Remote GitHub Actions (optional, manual)

| Workflow | Trigger | Purpose |
|---|---|---|
| `ci.yml` | **workflow_dispatch only** | Optional remote re-check (uv/ruff/tests + MSRV) |
| `release.yml` | **workflow_dispatch only** | Optional asset refresh for an **existing** tag |
| `gitleaks.yml` | **workflow_dispatch only** | Secret scan on demand |

**Do not** treat a green remote run as the release gate — run `local-ci.sh` on this machine.

---

## Day-to-day local checks (no release)

```bash
bash scripts/dev.sh sync          # uv env
bash scripts/dev.sh check         # ruff + tests
bash scripts/local-ci.sh          # full gate (preferred before push)
bash scripts/local-ci.sh --quick  # ruff + rust only
```

---

## Suggested post-v0.6.0 work

Ship patch/minor from this branch when local-ci is green:

- Package foundation + Wave 1 swarm merges (routing, usage registry, docs, CI-as-manual)
- Ongoing Python ports (#25 format, #26 send, #27 poll, …)

Until the next cut keep `VERSION=0.6.1-dev` and deploy with `deploy-local.sh` as needed.

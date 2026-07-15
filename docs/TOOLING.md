# Tooling — UV, Ruff, Rust

## Python (UV + Ruff)

| Tool | Role |
|---|---|
| **[uv](https://docs.astral.sh/uv/)** | Python 3.14 env, lockfile, `uv run` for scripts/tests |
| **[ruff](https://docs.astral.sh/ruff/)** | Lint + format (`target-version = "py314"`) |

### Setup

```bash
# Install uv if needed: https://docs.astral.sh/uv/getting-started/installation/
cd /path/to/tg-agent-relay
bash scripts/dev.sh sync          # creates .venv on Python 3.14
```

### Daily

```bash
bash scripts/dev.sh lint          # ruff check
bash scripts/dev.sh format        # ruff format + autofix imports
bash scripts/dev.sh test          # offline tests with RELAY_PYTHON=.venv/bin/python
bash scripts/dev.sh check         # lint + test

# Or direct:
uv run ruff check tg_agent_relay providers lib tests
uv run python -m tg_agent_relay.cli version
uv run pytest                     # when pytest suite is primary
```

### Version pin

- `.python-version` → `3.14`
- `requires-python = ">=3.14"` in `pyproject.toml`
- Runtime scripts still use `lib/python.sh` (prefers `.venv`, then `python3.14`)

Override: `RELAY_PYTHON=/path/to/python` or `uv run --python 3.14 …`.

### Agent note

Swarm agents should:

```bash
uv sync --all-groups
uv run ruff check --fix path/to/changed.py
uv run ruff format path/to/changed.py
```

Do not invent alternate linters (flake8/black) — Ruff only.

**Ruff 0.15.x format footgun:** `ruff format` rewrites multi-exception handlers
without an `as` target from valid Python 3:

```python
except (OSError, ValueError):      # valid
```

into invalid Python 2-style:

```python
except OSError, ValueError:        # SyntaxError on Python 3
```

**Always write:**

```python
except (OSError, ValueError) as _exc:
```

`scripts/local-ci.sh` py_compile-gates this; never skip that gate after `ruff format`.

## Rust (full toolchain)

| Pin | File |
|---|---|
| **MSRV** | **1.96** (`Cargo.toml` → `workspace.package.rust-version = "1.96"`) |
| Channel + components | `rust-toolchain.toml` (`1.96` + rustfmt, clippy, rust-src, rust-analyzer) |
| Workspace | `Cargo.toml` (members empty until epic #22) |

```bash
rustup show                    # respects rust-toolchain.toml → 1.96.x
bash scripts/dev.sh rust-check # fmt + clippy when crates exist
```

Install/update full toolchain:

```bash
rustup toolchain install 1.96
rustup component add --toolchain 1.96 rustfmt clippy rust-src rust-analyzer
# or: rustup show  # auto-installs from rust-toolchain.toml
```

Optional crates land under `crates/` and are added to `[workspace].members` only after benchmarks (epic #22).
Do not raise the MSRV without updating `rust-toolchain.toml`, `Cargo.toml`, CI, and this doc together.

## Local CI gate (preferred)

**Remote Actions are manual-only.** Quality and releases run on this workstation:

```bash
bash scripts/local-ci.sh              # full gate: uv, ruff, rust MSRV, offline tests, CLI smoke
bash scripts/local-ci.sh --quick      # ruff + rust only
bash scripts/local-ci.sh --release    # full gate + clean-tree preflight
bash scripts/release.sh vX.Y.Z        # re-runs local-ci, then tag + gh release from this machine
```

Also: `bash scripts/dev.sh local-ci`.

## Remote CI (optional, workflow_dispatch)

| Workflow | Trigger | Role |
|---|---|---|
| `.github/workflows/ci.yml` | **manual only** | Optional remote re-check (uv/ruff/tests + MSRV 1.96) |
| `.github/workflows/release.yml` | **manual only** | Optional asset refresh for an **existing** tag |
| `.github/workflows/gitleaks.yml` | **manual only** | On-demand secret scan |

Do not use remote green as the release gate — run `local-ci.sh` first.

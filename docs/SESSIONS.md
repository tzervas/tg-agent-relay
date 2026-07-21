# Multi-session @handles (Grok Build)

Run **several Grok Build sessions** on one host without every session seeing
the same Telegram traffic. Each session registers an **@handle**; `tg-poll`
routes matching messages to that session’s **dedicated FIFO** only.

Backward compatible: with no `[sessions]` section and no `.sessions.d/` files,
behavior is unchanged (single `[backends.grok]` FIFO or stdout).

## How it works

| Piece | Role |
|---|---|
| **Static `[backends.<id>]`** | Optional baseline in `relay.toml` (prefixes, fifo, tag) |
| **`.sessions.d/<handle>.json`** | Dynamic registry written by `register-session.sh` |
| **Effective backends** | Static merged with sessions; **same handle id → session file wins** |
| **Prefix routing** | Longest prefix match (`@cabal` vs `@cabal2`) then strip handle from body |

Inbound line on the FIFO (same as today):

```text
[telegram:backend:cabal:project:myrepo] implement the feature
```

## Mobile tweaks

From Telegram (with `[commands.config]` enabled — see [`COMMANDS.md`](COMMANDS.md)),
you can flip `routing.require_prefix` or usage window/chart prefs without SSH:
`/config`, `/config get routing.require_prefix`, `/config set routing.require_prefix true`.

With `routing.require_prefix = true` and per-session `@handle` prefixes, use either
`@cabal /config` or plain `/config` (when `require_prefix` is false). Prefixed
forms strip the handle before command dispatch, so handlers always see `/config`,
not `@cabal /config`.

## Quick start (two Grok sessions)

### 1. Relay config

In `relay.toml` (optional but recommended for unified chat):

```toml
[routing]
require_prefix = true   # optional: ignore unprefixed noise in shared chats

[sessions]
# dir = "~/.claude/telegram-bridge/.sessions.d"  # default under bridge dir: .sessions.d
```

You can also define static multi-handle backends entirely in TOML (no registry);
see [ROUTING.md](ROUTING.md#multi-session-grok-handles).

### 2. Register each session

From the bridge checkout (or deployed `~/.claude/telegram-bridge`):

```bash
# Session A — @cabal
bash scripts/register-session.sh --handle cabal

# Session B — @fleet
bash scripts/register-session.sh --handle fleet
```

Each run creates:

- `sessions/<handle>.fifo` (under the bridge dir by default)
- `.sessions.d/<handle>.json` (gitignored)

### 3. Attach a Monitor per FIFO

Use the command printed by `register-session.sh`:

```bash
adapters/backend-fifo-reader.sh /path/to/sessions/cabal.fifo
```

Repeat for `fleet.fifo` in the second Grok session’s Monitor.

### 4. Telegram

In your allowlisted chat:

```text
@cabal refactor the router
@fleet run integration tests
```

Only the matching session’s FIFO receives the line (body without `@handle`).

## Operator commands

```bash
bash scripts/list-sessions.sh
bash scripts/unregister-session.sh --handle cabal
bash scripts/register-session.sh --handle cabal --reclaim   # replace stale registration
```

`--reclaim` replaces a registration when the old `pid` is dead; if the old
process is still alive, registration fails unless you pass `--reclaim` after
stopping that session.

## Paths and deploy

| Path | Notes |
|---|---|
| `BRIDGE_DIR/.sessions.d/` | Default registry (gitignored) |
| `BRIDGE_DIR/sessions/*.fifo` | Default FIFOs from `register-session.sh` |
| `[sessions].dir` | Override in `relay.toml` |
| `RELAY_SESSIONS_DIR` | Env override (shell + Python) |

After `git pull`, restart `tg-poll` (or rely on its periodic config reload)
so new registrations are picked up. Deploy with `bash scripts/deploy-local.sh`
— preserves `.sessions.d/` and `sessions/` like `.chats.d/`.

## Inbound stack (required)

Multi-session routing only works when **both** sides of the pipe are running:

| Process | Role |
|---|---|
| **`tg-poll.sh`** | Reads Telegram; writes tagged lines to each backend FIFO |
| **`adapters/backend-fifo-reader.sh`** | One long-lived reader **per FIFO** (Monitor event source) |

If `tg-poll` writes while no reader holds the FIFO open, the kernel returns
**ENXIO** and the message is **dropped**. After deploy or reboot, start the
stack explicitly:

```bash
bash scripts/ensure-inbound.sh
# or from the bridge dir: bash scripts/ensure-inbound.sh --bridge-dir ~/.claude/telegram-bridge
```

`ensure-inbound.sh` starts **tg-poll** and **RDWR keepalives** on each unique
FIFO (no data drain). It does **not** start log-only readers — your harness
**Monitor** must run `adapters/backend-fifo-reader.sh <fifo>` or agent-bound
messages sit in the pipe buffer until a Monitor attaches. Pid files under
`.run/`; logs under `.run/logs/`.

Deploy also installs the Python package (`uv pip install -e .` into
`DEST/.venv` when possible) or sets `PYTHONPATH` via `lib/exec-env.sh` and
`env.sh` so `tg-poll` / `tg-send` import `tg_agent_relay` without a dev checkout.

## Mobile checklist

1. [ ] `require_prefix = true` if the chat is shared/noisy
2. [ ] One `register-session.sh --handle …` per Grok window
3. [ ] One `backend-fifo-reader.sh` Monitor per handle
4. [ ] Send `@handle …` — verify only that session reacts
5. [ ] `list-sessions.sh` shows `pid` alive/dead

## See also

- [ROUTING.md](ROUTING.md) — prefixes, FIFO delivery, static multi-backend
- [SETUP.md](../SETUP.md) — bridge install
- `relay.toml.example` — commented multi-handle example
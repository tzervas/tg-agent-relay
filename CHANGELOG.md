# Changelog

## [Unreleased]

## 0.10.3 — 2026-07-25

Security + correctness patch, no new features. PATCH per commitizen
(`cz bump --dry-run` → `increment detected: PATCH`).

### Security
- **Code injection in the goal-noise filter call sites** (`relay-notify.sh`,
  `adapters/claude-code.sh`). Harness-supplied strings were interpolated into
  the *text* of a `python -c` program —
  `filter_hook_summary(s, tool_name='${_CC_TOOL}', …)`. `tool_name` is whatever
  tool the model asked for, including a name supplied by an MCP server, so a
  payload of

  ```
  x' if __import__('pathlib').Path('/tmp/pwned').write_text('x') else 'x
  ```

  closed the string literal and executed inside the relay's own interpreter
  while the notification still delivered normally. Both sites now pass values
  through the **environment**. `tests/run-tests.sh` asserts the payload does not
  execute; both new assertions were confirmed red against 0.10.2.

### Fixed
- **The relay could go silently deaf.** When `tg_agent_relay` was not importable
  — a deployed bridge without the package on its path, a partial deploy, a bare
  `python -c` — the goal-noise filter's non-zero exit was read as "policy said
  drop this" and **every** hook notification was discarded, with nothing logged
  to say why. The filter now fails *open*: only an explicit exit code 3 (policy
  returned `None`) silences a message, and `RELAY_DEBUG=1` reports a fail-open.
- **`cfg["sessions"]["dir"]` was ignored.** `lib/routing.py` gated the session
  overlay on `_bridge_dir` alone, but `sessions_dir_from_cfg()` resolves
  `cfg["sessions"]["dir"] → $RELAY_SESSIONS_DIR → <bridge>/.sessions.d → $HOME`.
  A caller that configured sessions the documented way got no session backends
  at all — `strip_prefix()` returned `None` and `resolve()` produced an empty
  backend. The overlay is now gated on any of the three **explicit** sources,
  never the implicit `$HOME` default (which would let a config that says nothing
  about sessions inherit whatever handles are registered on the host).
  `has_routing_config()` carried the same stale gate and now matches.
- **Inbound FIFO honesty:** successful non-blocking FIFO writes no longer imply
  an agent TUI received the message. When only a keepalive (or no process)
  holds the pipe open for read, `poll.py` still returns write success but emits
  `message_orphaned backend=… reason=no_agent_reader`. Attach a Monitor with
  `adapters/backend-fifo-reader.sh <fifo>` (or `tgar-session@`).

### CI
- `fleet-ci.yml` python gate is **fail-closed**. `uv sync … || true` and
  `pytest -q 2>/dev/null || … || echo "mark job fail"` could not fail a job —
  `echo` is not a gate — so a green `python lint/test` badge on `main` was
  reporting nothing. The `uv run ruff check . || python -m ruff check .`
  fallback went with it: `||` fires on any non-zero exit, conflating "ruff found
  problems" with "ruff is not installed". Missing tooling is now `FAIL_ENV`, a
  degraded `uv sync` retry is `DEGRADED_ENV`, and the no-stack job is labelled
  `SKIP_STUB` / "not a product gate".
- `trivy filesystem` renamed to "(advisory)" — it is `continue-on-error: true`
  and never could fail the workflow. The name now says so.

### Style
- `ruff format` applied to `tests/test_fifo_agent_readers.py` and
  `tg_agent_relay/poll.py`, the only two files that had drifted. Nothing on
  `main` ran `ruff format --check`, so the drift was invisible until a gate
  restored the check.

### Added
- `lib/fifo_agent_readers.py` — pure helpers + Linux `/proc/*/fd` scan
  distinguishing agent readers (`backend-fifo-reader`, `tgar-session@`) from
  ensure-inbound keepalives; used by `poll.py` (`fifo_has_agent_reader`).
- `scripts/doctor-inbound.sh` — prints `default_backend`, per-FIFO agent reader
  counts, and Monitor commands for fleet/cabal; exit 1 if the default backend
  FIFO has no agent reader.
- `scripts/inbound-health.sh` — per-backend/session keepalive, agent_reader,
  and orphan metric report; exit 1 when any fifo target lacks a reader.
- `ensure-inbound.sh` ERROR lines when `default_backend` / cabal / fleet have
  no agent reader; points at `doctor-inbound.sh` / `inbound-health.sh`.
- Docs + `relay.toml.example`: multi-agent orch recommends
  `default_backend = "fleet"` (general Grok); cabal is the L0 coding leaf.
  Untagged messages need a Monitor on the default backend FIFO.
- `docs/SESSIONS.md` — “Why my Grok gets nothing” troubleshooting
  (`default_backend`, `@fleet` prefix, Monitor, health checks).
- Offline unit tests: `tests/test_fifo_agent_readers.py` + orphan metric cases
  in `tests/test_poll.py`.

## 0.10.2 — 2026-07-21

### Fixed
- `RELEASE_NOTES.md` had skipped straight from v0.9.0 to v0.10.1, missing
  standalone entries for v0.10.0 and v0.10.1; backfilled both so per-version
  deploy notes are complete for operators upgrading step by step.

No functional/code changes vs 0.10.1.

## 0.10.1 — 2026-07-20

### Fixed
- **Inbound to agent harnesses:** `ensure-inbound` no longer starts log-draining FIFO readers that steal messages from Grok/Claude Monitors. It only runs `tg-poll` and RDWR keepalives (no read).
- Dual readers on the same FIFO path are deduped; agent Monitors own the read path.
- Avoid `pgrep -f` in ensure-inbound (self-matches bash wrappers).

### Added
- `/status` as relay-mode alias of `/stats` (zero-token).
- README architecture diagram: relay vs agent split, keepalives, multi-backend FIFOs (sanitized).

## [0.10.0] — 2026-07-16

### Added

- **Forum threads (P13)** — `tg_agent_relay/threads.py`: topic title builders,
  outbound resolve order, mockable `createForumTopic`, overlay bind helpers.
- **`/thread` commands** — `handlers/thread.sh` + `[commands.thread]` example:
  `list`, `here`, `bind`, `ensure`.
- **Outbound thread routing** — `relay-notify.sh` sets `RELAY_CHAT_ID` /
  `RELAY_THREAD_ID` from `RELAY_SESSION`, `RELAY_PLATFORM`, `RELAY_WORKSTREAM`,
  `RELAY_AGENT_HANDLE` with optional `🧵` title stamp.
- **Docs** — `docs/THREADS.md` and ROUTING.md cross-link.

## [0.9.0] — 2026-07-16

### Added

- **Goal noise handling** — benign `update_goal` / inactive-goal tool failures
  are suppressed or softened on hook sends (`tg_agent_relay/goal_events.py`,
  `provider_hook.py`, adapters, `relay-notify.sh`).
- **Plan approve via Telegram** — PLAN outbound messages attach Approve /
  Reject / Later keyboards; text and callback replies emit
  `[telegram:plan] status=…` (`tg_agent_relay/plan_approve.py`, `poll.py`).
- **Usage chart buttons** — usage PNGs include 24h / 7d / 30d / Refresh
  inline keys (`handlers/usage.sh`, `handlers/dashboard.sh`).

### Changed

- **TTS** — broader emoji stripping (incl. 🏁); auto `spoken_mode=full` for
  PLAN / multi-page / very long bodies with multi-clip voice for direct sends
  too (`lib/tts_plain_text.py`, `tg_agent_relay/tts.py`, `tg-send.sh`,
  `send.py`).
- **Formatting** — clearer PR / PLAN / GOAL headers, PR URLs on their own line
  (`tg_agent_relay/comms_format.py`).
- **Outbound** — optional `RELAY_REPLY_MARKUP_JSON` on first `sendMessage` /
  `sendPhoto` page (`tg-send.sh`, `send.py`).

## [0.8.1] — 2026-07-16

### Fixed

- **Prefixed commands** — `@handle /config` and `@handle /usage` strip the
  session prefix before command classify/dispatch (Python `poll.py` and shell
  `tg-poll.sh` parity).
- **Handlers** — `handlers/config.sh` and `handlers/usage.sh` source
  `lib/exec-env.sh` + `lib/python.sh`; config surfaces real Python stderr on
  failure; usage explains missing matplotlib / dashboard extra.
- **Deploy** — `deploy-local.sh` installs `.[dashboard]` extras and restarts
  inbound poll via `ensure-inbound.sh --restart-poll`.

## [0.8.0] — 2026-07-16

### Added

- **Usage dashboard** — allotments per provider/period, multi-source text
  breakdown, quota bars, and chart modes (`bar` / `line` / `both` /
  `allot` / `share`) with padded PNG screenshots when matplotlib is
  available.
- **Remote config** — Telegram `/config` get/set for an allowlisted subset
  of `relay.toml` (charts default, usage window, allotments); see
  `handlers/config.sh` and `lib/remote_config.py`.
- **Docs** — expanded `docs/USAGE.md`, `docs/COMMANDS.md`, and
  `relay.toml.example` for usage + `/config`.

### Changed

- `lib/usage_ingest.py` and `lib/dashboard_render.py` extended for
  allotment-aware aggregation and chart rendering.
- `handlers/usage.sh` supports new chart and breakdown modes.

## [0.7.0] — 2026-07-16

### Added

- **Multi-session @handles** — dynamic `.sessions.d/<handle>.json` registry
  merges over static `[backends.*]` (session wins on same id); `@cabal` /
  `@fleet` route to separate FIFOs via longest-prefix match.
- **Scripts** — `scripts/register-session.sh`, `unregister-session.sh`,
  `list-sessions.sh`.
- **Docs** — `docs/SESSIONS.md` operator guide; multi-session section in
  `docs/ROUTING.md` and `relay.toml.example`.

### Changed

- `load_config` / `load_relay_config` apply session backends at load time.
- `lib/routing.py` `resolve` / `strip_prefix` use effective merged backends.
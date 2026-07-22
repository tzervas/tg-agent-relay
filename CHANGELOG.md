# Changelog

## [Unreleased]

### Fixed
- **Inbound FIFO honesty:** successful non-blocking FIFO writes no longer imply
  an agent TUI received the message. When only a keepalive (or no process)
  holds the pipe open for read, `poll.py` still returns write success but emits
  `message_orphaned backend=… reason=no_agent_reader`. Attach a Monitor with
  `adapters/backend-fifo-reader.sh <fifo>` (or `tgar-session@`).

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
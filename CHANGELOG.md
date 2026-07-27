# Changelog

## [Unreleased]

### Added
- **Dedicated bots per agent** (`docs/MULTI_BOT.md`). Claude and Grok can now
  run on separate @BotFather bots, each with its own Telegram chat and
  notification stream, instead of multiplexing one bot with `@handle` prefixes.
- `tg_agent_relay/bots.py` — bot identity: per-bot token env resolution,
  backend↔bot binding (`backends.<id>.bot`, `routing.default_bot`), and state
  isolation. Bot ids are sanitised so a `relay.toml` value cannot escape the
  state root.
- **Per-bot state directories.** Telegram's `getUpdates` cursor is per bot, so
  two poll loops sharing one `<bridge>/.offset` would each advance past updates
  the other never saw and eat messages silently; per-chat reassembly buffers
  collide the same way when both bots are in one group. Each bot now owns
  `<bridge>/.bots/<id>/`. Isolation is a directory rather than a filename
  suffix, so existing globs work unchanged.
- `scripts/ensure-inbound.sh` starts one poll loop per configured bot, each
  with its own pidfile and log (`tg-poll-<bot>.pid` / `.log`).
- Outbound token selection follows the same binding: `RELAY_BOT`, else derived
  from `RELAY_BACKEND`, else `BOT_TOKEN`.
- Cross-channel delivery is refused — a message arriving on one bot is never
  delivered into a backend owned by another, recorded as
  `message_filtered backend=… bot=… want=…`.
- `tests/test_bots.py` — 49 offline assertions, including that two bots'
  offsets are genuinely independent and that the whole feature is inert
  without a `[bots.*]` table.

**Back-compat:** with no `[bots.*]` table nothing changes — one `BOT_TOKEN`,
one poll loop, the same `.offset` / `.tg-buffer*` paths and the original
`tg-poll.pid` / `tg-poll.log` names.

## 0.10.3 — 2026-07-27

### Fixed
- **Inbound messages reached the queue but not the session.** `ensure-inbound`
  holds every backend FIFO open `RDWR` (keepalive) so writers never `ENXIO`,
  and `deliver_to_backend` treated a successful write as delivery. With a
  keepalive attached the write always succeeds — into the 64K kernel pipe
  buffer, where nothing consumes it. Lines waited there until a Monitor
  attached and drained the backlog as one burst, or were **dropped
  permanently** once the buffer filled. There was no spool, retry or ack.
  Both pollers now check for a real agent reader *before* writing and spool
  when there is none; `message_delivered` is emitted only when a reader was
  attested **and** the write landed.
- `tg-poll.sh` emitted `message_delivered` unconditionally — including on the
  failure branch that had just emitted `deliver_skip` — and had no orphan
  detection at all.
- **Registered `@handle` sessions were invisible to the routing API.**
  `lib/routing.py` gated the `.sessions.d` overlay on `cfg["_bridge_dir"]`,
  but `sessions.dir` and `RELAY_SESSIONS_DIR` resolve without it (and are
  preferred over it). Any caller not going through `load_config`/`poll` lost
  every registered handle: `strip_prefix` returned `None`, `resolve` fell
  through to `default_backend`, and `merged_backends` disagreed with `resolve`
  on the same config. Affected the documented `resolve` join API and
  `python -m tg_agent_relay.routing --config`. Live inbound routing was not
  affected (`poll.py` injects `_bridge_dir`). Fixes the three long-standing
  `tests/test_sessions_routing.py` failures at the source.
- `scripts/doctor-inbound.sh`: `${VAR:-{}}` left a stray brace, so `jq` failed
  with `Unmatched '}'` on every run without a `relay.toml`.

### Added
- `tg_agent_relay/spool.py` — durable per-backend inbound spool: ordered
  replay, atomic claim (concurrent drains never double-deliver), emit failure
  leaves the line pending, stale claims from a dead drainer reclaimed, bounded
  by `RELAY_SPOOL_MAX` with a loud `spool_overflow` rather than silent
  truncation, and backend ids sanitised so a `relay.toml` value cannot escape
  the spool root. CLI: `put` / `drain` / `count`.
- `adapters/backend-fifo-reader.sh` drains the spool on attach and on an
  interval, so a session replays what it missed, in order. Installs no signal
  trap deliberately — the loop blocks in `read < fifo` and bash defers traps
  until the foreground command returns, so a `TERM` trap makes an idle Monitor
  unkillable; the drain child watches for parent death instead.
- Arrival-order guarantee across both channels: while a replay is draining,
  new messages append to the spool (`message_spooled_ordered`) instead of
  jumping the queue via a direct FIFO write.
- `scripts/doctor-inbound.sh` reports spool depth per backend.
- `docs/INBOUND-DELIVERY.md` — operator guide: diagnosis, live verification,
  metric semantics, ordering, tuning.
- `tests/test_spool.py` — 33 offline assertions (ordering, claim races, emit
  failure, reclaim, overflow, path traversal, partial writes).

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
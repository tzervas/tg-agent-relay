# Changelog

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
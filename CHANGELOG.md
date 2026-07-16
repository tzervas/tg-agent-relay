# Changelog

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
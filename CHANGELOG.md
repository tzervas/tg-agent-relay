# Changelog

## [0.7.0-dev] — unreleased

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
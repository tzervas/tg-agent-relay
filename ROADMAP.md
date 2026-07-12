# TG Agent Relay — Roadmap

Goal: a **platform/agent/harness-agnostic** Telegram relay — integrate
with ANY agent using ANY harness, maximum portability + usability.

## Done

- `tg-send.sh` — outbound curl→Telegram, `[k/n]` pagination, dedup,
  no-op-without-token. Harness-neutral.
- `tg-poll.sh` — inbound long-poll, strict user-id allowlist, burst
  reassembly, emits plain `[telegram] <text>` on stdout, or
  `[telegram:cmd:<tag>] <text>` for a recognized in-chat command.
  Harness-neutral (stdout is universal); sourceable for tests.
- **Publish + rename.** Public MIT repo, gitleaks-scanned; renamed
  `claude-telegram-bridge` → `tg-agent-relay` (GitHub auto-redirects the
  old URL). Local working directory stays `~/.claude/telegram-bridge/`
  deliberately — see README's repo/directory note.
- **Agent/harness-agnostic core.** `relay-notify.sh` — a generic entry
  point ANY agent/harness can call directly with free text or
  `{"label":..,"text":..}` JSON. The Claude-Code-specific hook-JSON
  parsing moved out of `hook-notify.sh` into `adapters/claude-code.sh`
  (one adapter among possibly several); `hook-notify.sh` is now a thin,
  backward-compatible shim that `exec`s it, so the live
  `~/.claude/settings.json` hook wiring is unchanged. `adapters/README.md`
  + `adapters/generic-example.sh` document/scaffold writing a new adapter
  for any other harness.
- **Configurable hooks (`relay.toml`).** `relay.toml.example` (committed)
  documents `[general]` (page size/delay, reassemble window, hook page
  cap), `[generic]` (the harness-agnostic prefix), `[claude_code.<Event>]`
  (per-event enable + prefix, covering the documented Claude Code hook
  event set — see `adapters/claude-code.sh`'s header for the full list and
  which are actually wired vs. available to wire), and `[commands.<name>]`
  (in-chat commands). Every script falls back to its pre-existing
  env-var/hardcoded default with no `relay.toml` present — the
  backward-compat guarantee.
- **In-chat commands (user→agent).** `tg-poll.sh` recognizes a leading
  `/slash` command OR a configured keyword prefix (`status ...`) from
  `relay.toml`'s `[commands.*]` tables and tags the emitted event
  (`[telegram:cmd:<tag>] ...`); an unmatched message (or no `relay.toml`
  at all) still emits the plain `[telegram] <text>` it always has.
- **Relay-handled-command SEAM.** `dispatch_command()` in `tg-poll.sh`
  routes a command by its `relay.toml` `mode`: `"forward"` (default) tags
  + emits to the agent as above; `"relay"` runs a local `handler` script
  instead and emits nothing to the agent — zero model tokens. Four real
  handlers now use this seam (see the metrics-dashboard entry below); see
  `handlers/README.md` for the contract and `handlers/example-echo.sh`
  (test-only) for the minimal shape one takes.
- **Metrics event hook point.** `lib/relay-common.sh`'s
  `emit_metric <source> <event> [detail]` appends a TSV line
  (epoch/source/event/detail) to `.metrics.log` on every
  send/poll-flush/hook/command/poll-error — non-blocking, best-effort,
  gitignored. Wired into `tg-send.sh`, `tg-poll.sh` (flush, both
  command-dispatch paths, and the two `getUpdates` failure paths —
  `poll_error`), `relay-notify.sh`, and `adapters/claude-code.sh`.
- **The relay-side metrics dashboard (zero model tokens).** Four real
  `handlers/*.sh` commands, all registered in `relay.toml.example`:
  `/dashboard` (a multi-panel dashboard — header stats, volume-over-time,
  hook-event breakdown, command usage — rendered as a dark-friendly
  matplotlib PNG via `sendPhoto`, with a graceful unicode/text fallback
  via `sendMessage` when matplotlib is unavailable; never fails to send
  something), `/stats` (the key numbers only, lighter/text-only),
  `/uptime` (poll-daemon uptime — real process elapsed time, or an
  honestly-labeled `.metrics.log` proxy if no process is found), and
  `/help` (lists every configured command live from `relay.toml`, never
  stale). The aggregation (`lib/metrics_agg.py`) is pure/stdlib-only and
  unit-tested independently of matplotlib (`tests/test_metrics_agg.py`);
  the image renderer (`lib/dashboard_render.py`) imports it and degrades
  to the same text renderer on any render failure. "Model-turns avoided"
  is tracked as a `Declared` heuristic (enabled-hook pings +
  relay-handled commands), never presented as a measured token count.

## Next

- **Deeper per-event message templating.** Today `[claude_code.<Event>]`
  only overrides `enabled`/`prefix`; the rest of each event's message
  shape is still code-defined in `adapters/claude-code.sh`. A `format`
  string with `{field}` interpolation (à la Python `str.format`) would let
  users fully customize wording without editing the adapter — worth doing
  once there's a second or third adapter to validate the design against a
  real second use case, so the template mini-language earns its
  complexity rather than being speculative.
- **More adapters.** A generic-webhook adapter (accepts any POSTed JSON on
  a local pipe/FIFO — still outbound-only, no listening port opened) and
  a plain-log-tail adapter (watches a file, forwards new lines) would
  cover most non-Claude-Code harnesses without a bespoke adapter each
  time.
- **Command → behavior wiring on the consuming side.** The relay tags a
  command (`[telegram:cmd:status]`); actually *acting* on it (e.g. a
  Claude Code session recognizing that tag and running a status check) is
  the consuming agent's job, not the relay's — documenting a reference
  pattern for that (a `Monitor` source's prompt/instructions noticing the
  tag) is worth adding once there's a real example to point to.
- **Per-project hook scoping.** SETUP.md's "Caveat: the hooks are global"
  still applies — narrowing `~/.claude/settings.json`'s hooks to a
  project-local `.claude/settings.json` (so only one repo's sessions get
  pinged) is a documented option, not yet a first-class flow.

## Principles

- Security: id-allowlist, token in local `.env` (gitignored,
  gitleaks-protected), outbound-only (no inbound port). `relay.toml`
  holds no secret — see `.gitignore`.
- Workspace-tools "productionize + publish" pattern: mocked/fixtured
  offline tests (`tests/`), MIT, public, reusable.
- Never-silent, honest; small auditable scripts (KISS). Every new surface
  (generic core, adapters, config, commands) is additive and
  backward-compatible — an existing invocation never silently changes
  behavior; a new default only ever applies when nothing configured it
  otherwise.

# Handlers (relay-handled commands)

**Status: shipped.** `tg-poll.sh`'s `dispatch_command()` routes a matched
`[commands.<name>]` to a local handler instead of forwarding it to the
agent (`mode = "relay"` in `relay.toml` — see `relay.toml.example`). Five
real handlers ship in this directory:

- **`dashboard.sh`** — a multi-panel metrics dashboard: header stat row,
  volume-over-time, hook-event breakdown, command usage. Renders a
  matplotlib PNG (dark-friendly, mobile-legible) via `sendPhoto` when
  matplotlib is available, or a unicode/text dashboard via `sendMessage`
  otherwise — never fails to send something. Optional trailing window
  override, e.g. `/dashboard 48` for the last 48 hours. Also appends
  token-usage panels when `[usage].enabled = true` (see `usage.sh` below).
- **`stats.sh`** — the key numbers only, as plain text (lighter than
  `/dashboard`). Same window override.
- **`uptime.sh`** — how long the poll daemon (`tg-poll.sh`) has been
  running (real process elapsed time, or an honestly-labeled proxy from
  `.metrics.log` if no process is found).
- **`help.sh`** — lists every configured command (relay-handled +
  forwarded), read live from `relay.toml` so it never drifts from what's
  actually enabled.
- **`usage.sh`** — an OPT-IN (`[usage].enabled = true`, default off) token
  USAGE dashboard: tokens by provider/model/project, over `lib/usage_ingest.py`'s
  aggregation of a harness's local session-transcript logs (one adapter
  ships today, Claude Code's own `~/.claude/projects/**/*.jsonl`). Same
  image-with-text-fallback contract as `dashboard.sh`. **PRIVACY:**
  everything reads/writes stays local and gitignored — see
  `docs/USAGE.md`'s "Token usage dashboard" section before enabling it.

`dashboard.sh`/`stats.sh`/`uptime.sh`/`help.sh` read `.metrics.log` (via
`lib/metrics_agg.py`, the pure/testable aggregation module); `usage.sh`
(and `dashboard.sh`'s optional usage panels) read a harness's local
transcript logs via `lib/usage_ingest.py` (a separate, opt-in aggregation
module — see its module docstring for the source-adapter contract). All
five are registered in `relay.toml.example`. This file documents the
contract a new handler follows.

## Why this exists

Every command today is **forwarded**: `tg-poll.sh` tags the message and
emits it on stdout for the agent/`Monitor` to read and act on — which
costs a model turn. Some commands (a status dashboard, an uptime check, a
metrics summary) don't need a model at all; the *relay itself* has enough
information to answer directly. Routing those to a **handler** instead of
the agent means they cost **zero model tokens**, the same reasoning that
already makes outbound hook pings free.

## The contract

1. **Registration** (`relay.toml`):

   ```toml
   [commands.dashboard]
   keyword = "dashboard"
   slash = "/dashboard"
   mode = "relay"
   handler = "handlers/dashboard.sh"   # relative to the bridge dir, or absolute
   ```

   `mode` defaults to `"forward"` (today's only real behavior) if omitted
   — a command with no `mode` key, or `mode = "forward"`, is untouched by
   any of this.

2. **Invocation.** When a flushed inbound message matches a `mode =
   "relay"` command, `tg-poll.sh` runs:

   ```
   <handler-path> "<flattened message text>"
   ```

   **detached and backgrounded** (`&`, stdout/stderr discarded) so a
   slow or hung handler can never block the poll loop. Nothing is
   printed to `tg-poll.sh`'s own stdout for a relay-handled command — the
   agent/`Monitor` never sees the event at all.

3. **Replying.** A handler that wants to answer the user calls
   `relay-notify.sh` (or `tg-send.sh` directly) itself — it has the same
   `$BRIDGE_DIR` layout available (it's invoked from within this repo).
   A handler that doesn't need to reply (e.g. one that just logs
   something) may do nothing.

4. **Failure handling.** A missing/non-executable `handler` is a silent
   no-op (never a crash) — `dispatch_command()`'s fail-safe. A handler
   that itself errors is the handler's own problem; it should not assume
   `tg-poll.sh` will report anything on its behalf (it's detached, exit
   code is discarded).

5. **No shared state assumed.** A handler runs as its own process; if it
   needs the bridge's runtime state (`.offset`, `.last-sent`, a future
   metrics log — see `lib/relay-common.sh`'s `emit_metric`), read it the
   same way any other script here does (paths relative to `$BRIDGE_DIR`).

## Writing a new one

Copy the shape of `dashboard.sh`/`stats.sh` (real handlers) rather than
`example-echo.sh` (used only by `tests/run-tests.sh` to prove the dispatch
seam works end-to-end — not a real command, not wired into
`relay.toml.example`), and follow `adapters/README.md`'s general "small,
disposable script" guidance. If it needs metrics, read `.metrics.log`
through `lib/metrics_agg.py` (`parse_log`/`filter_window`/`aggregate`)
rather than re-parsing the TSV by hand — one source of truth for what the
log's columns mean.

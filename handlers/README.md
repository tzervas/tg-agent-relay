# Handlers (relay-handled commands)

**Status: extension SEAM, not a shipped feature.** `tg-poll.sh`'s
`dispatch_command()` already knows how to route a matched
`[commands.<name>]` to a local handler instead of forwarding it to the
agent (`mode = "relay"` in `relay.toml` — see `relay.toml.example`), but
no real handler ships in this repo yet. A follow-up will add the actual
zero-token, relay-side commands (`/dashboard`, `/stats`, `/metrics`,
`/uptime`, `/help`, ...) as handlers in this directory. This file
documents the contract so that work drops in without touching
`tg-poll.sh` again.

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

## Writing one (once this becomes real work)

Copy the shape of `example-echo.sh` (used only by `tests/run-tests.sh` to
prove the dispatch seam works end-to-end — not a real command, not wired
into `relay.toml.example`) and follow `adapters/README.md`'s general
"small, disposable script" guidance.

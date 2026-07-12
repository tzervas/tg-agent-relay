# Default commands

Every in-chat command is defined in `relay.toml`'s `[commands.<name>]`
tables — see [`relay.toml.example`](../relay.toml.example) for the full
schema. **With no `relay.toml` (or no `[commands.*]` section), no command
exists at all** — every message is plain-forwarded, the original
behavior.

## Relay-handled vs. forwarded

Every matched command has a `mode`:

| `mode` | Who answers | Model tokens |
|---|---|---|
| `"forward"` (default, or `mode` omitted) | Tagged `[telegram:cmd:<tag>] <text>` and emitted to the agent/`Monitor` — the agent decides what to do with it. | Costs a turn (same as any inbound message). |
| `"relay"` | The relay itself runs the configured `handler` script and replies directly (`sendMessage`/`sendPhoto`). **Nothing is emitted to the agent** — it never even sees the event. | **Zero.** |

`tg-poll.sh`'s `dispatch_command()` is what implements this routing: it
reads each matched command's `mode` from `relay.toml` and either prints
the forwarded line or backgrounds the handler script
(`<handler-path> "<flattened text>" &`, detached, output discarded, so a
slow/hung handler can never block the poll loop).

## The four built-in relay-handled commands

None are enabled by default — uncomment their blocks in `relay.toml`
(copy from `relay.toml.example`, which documents all four together) to
turn them on. All four read `.metrics.log` via the pure, independently
unit-tested `lib/metrics_agg.py`.

### `/dashboard [<N>[h]]`

A multi-panel metrics dashboard: header stat row, message volume over
time, hook-event breakdown by type, command usage. Renders a
dark-friendly, mobile-legible PNG via `matplotlib` and sends it with
`sendPhoto`; if `matplotlib`/`python3` are unavailable or rendering fails
for any reason, falls back to a unicode/text dashboard sent via
`sendMessage` instead — it never fails to send *something*. Accepts an
optional trailing window override in hours (`/dashboard 48` → last 48
hours); defaults to `relay.toml`'s `[dashboard].window_hours` (or
`TG_DASHBOARD_WINDOW_HOURS`, or 24).

```toml
[commands.dashboard]
keyword = "dashboard"
slash = "/dashboard"
mode = "relay"
handler = "handlers/dashboard.sh"
```

### `/stats [<N>[h]]`

The same underlying numbers as `/dashboard`, as plain text only — no
image, no bars, lighter and faster. Same optional window override.

```toml
[commands.stats]
keyword = "stats"
slash = "/stats"
mode = "relay"
handler = "handlers/stats.sh"
```

### `/uptime`

How long the poll daemon (`tg-poll.sh`) has been running. Prefers the
**real process elapsed time** of a running `tg-poll.sh` (via `ps`); if no
matching process can be found (run standalone, or `ps` unavailable), it
falls back to the earliest `.metrics.log` entry as an explicitly-labeled
proxy ("earliest .metrics.log entry was ... ago (not real process
uptime)") — it never presents an estimate as if it were the measured
value.

```toml
[commands.uptime]
keyword = "uptime"
slash = "/uptime"
mode = "relay"
handler = "handlers/uptime.sh"
```

### `/help`

Lists every configured command — both relay-handled and forwarded — read
live from `relay.toml`, so it never drifts from what's actually enabled.
With no `[commands.*]` section at all, it says so explicitly rather than
showing a stale or empty-looking list.

```toml
[commands.help]
keyword = "help"
slash = "/help"
mode = "relay"
handler = "handlers/help.sh"
```

### Example: `/dashboard` reply

```
You:  /dashboard 48
Bot:  [PNG] Relay Dashboard — last 48h
```

### Example: `/help` reply (with all four enabled)

```
🧭 TG Agent Relay — help

Relay-handled (zero model tokens):
  /dashboard — dashboard (zero model tokens)
  /stats — stats (zero model tokens)
  /uptime — uptime (zero model tokens)
  /help — help (zero model tokens)

Any other message is forwarded to the agent as-is.
```

## Defining your own command

Add a `[commands.<name>]` table to `relay.toml`. `<name>` is the table
key (used internally); `slash`/`keyword` are what the user actually
types; `tag` (forward mode only) is what shows up in the emitted event,
defaulting to `<name>` if omitted.

**Forwarded** (the agent handles it — e.g. you want Claude Code to
actually run a status check when you type `/status`):

```toml
[commands.status]
keyword = "status"     # matches "status " or "status: ..."
slash = "/status"       # matches "/status" or "/status <args>"
tag = "status"           # -> "[telegram:cmd:status] status: how's it going?"
```

A message of `status: how's it going?` then emits, on `tg-poll.sh`'s
stdout:

```
[telegram:cmd:status] status: how's it going?
```

Your agent's `Monitor`/hook logic is what actually *acts* on that tag —
the relay only recognizes and labels the command, it doesn't know what
"status" means to your agent.

**Relay-handled** (the relay answers directly — e.g. a custom local
script that doesn't need the model at all):

```toml
[commands.mycheck]
keyword = "mycheck"
slash = "/mycheck"
mode = "relay"
handler = "handlers/my-check.sh"    # relative to the bridge dir, or absolute
```

Then write `handlers/my-check.sh` following the contract in
[`../handlers/README.md`](../handlers/README.md):

1. It receives the flattened command text as `$1`.
2. If it wants to reply, it calls `relay-notify.sh` (or `tg-send.sh`)
   itself — it has the same `$BRIDGE_DIR` layout available.
3. A missing/non-executable `handler` is a silent no-op, never a crash.
4. It runs detached and backgrounded — a slow or hung handler can never
   block the poll loop, but its own exit code/output is discarded, so it
   must not assume anything is reported on its behalf.
5. If it needs metrics, read `.metrics.log` through
   `lib/metrics_agg.py`'s `parse_log`/`filter_window`/`aggregate` rather
   than re-parsing the TSV by hand.

Copy the shape of `handlers/dashboard.sh`/`handlers/stats.sh` (real
handlers) rather than `handlers/example-echo.sh` (a test-only fixture
used by `tests/run-tests.sh` to prove the dispatch seam works, not
registered in `relay.toml.example`).

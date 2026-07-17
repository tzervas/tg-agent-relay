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

## The five built-in relay-handled commands

None are enabled by default — uncomment their blocks in `relay.toml`
(copy from `relay.toml.example`, which documents all five together) to
turn them on. `/dashboard`, `/stats`, `/uptime`, and `/help` read
`.metrics.log` via the pure, independently unit-tested
`lib/metrics_agg.py`. `/usage` additionally needs `[usage] enabled = true`
— see [`USAGE.md`'s "Token usage dashboard"
section](USAGE.md#token-usage-dashboard) for what it collects and the
privacy note before turning it on.

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

### `/config` — remote settings (relay.toml)

**Relay-handled, zero model tokens.** View or change **allowlisted**
`relay.toml` keys from Telegram. Never shows or writes secrets (`BOT_TOKEN`,
`ALLOWED_USER_ID`, `.env`).

| Command | Action |
|---|---|
| `/config` | Safe summary of current settings |
| `/config get <dotted.key>` | One value |
| `/config set <dotted.key> <value>` | Persist to `relay.toml` (`.bak` backup first) |
| `/config charts <bar\|line\|both>` | `usage.charts.default` |
| `/config usage window <7d\|30d\|…>` | `usage.window` |
| `/config allot <provider> <period> <n>` | `usage.allotments.<provider>.<period>` |
| `/config help` | Allowlisted keys |

```toml
[commands.config]
keyword = "config"
slash = "/config"
mode = "relay"
handler = "handlers/config.sh"
```

With `routing.require_prefix = true` and per-backend `@handle` prefixes (static
`[backends.cabal]` or a registered session), **`@cabal /config` works the same
as `/config`**: the poll loop strips the handle before classifying commands, so
`handlers/config.sh` always receives `/config`, not the prefixed line. Unprefixed
agent traffic still needs `@cabal …` when `require_prefix` is on — see
[`SESSIONS.md`](SESSIONS.md).

### `/usage [today|all|<N>d|<N>h]`

**OPT-IN — default disabled.** A token USAGE dashboard: tokens by
provider, model, and project, aggregated from a harness's local
session-transcript logs (one adapter ships today — Claude Code's own
`~/.claude/projects/**/*.jsonl`). Same image-with-text-fallback contract
as `/dashboard`. With `[usage].enabled` left `false` (the default),
replies that usage tracking is disabled rather than silently doing
nothing. Accepts an optional trailing window override — `today` (since
local midnight), `all` (everything the source has), or `<N>d`/`<N>h`
(e.g. `/usage 30d`); defaults to `relay.toml`'s `[usage].window` (or
`"7d"`).

**Read [`USAGE.md`'s "Token usage dashboard"
section](USAGE.md#token-usage-dashboard) — especially its privacy note —
before enabling this.** Everything it reads/writes stays local and
gitignored; nothing is ever transmitted anywhere but this relay's own
allowlisted Telegram chat.

```toml
[usage]
enabled = true

[commands.usage]
keyword = "usage"
slash = "/usage"
mode = "relay"
handler = "handlers/usage.sh"
```

### Example: `/dashboard` reply

```
You:  /dashboard 48
Bot:  [PNG] Relay Dashboard — last 48h
```

### Example: `/usage` reply

```
You:  /usage 30d
Bot:  [PNG] Token Usage — 30d
```

### Example: `/help` reply (with all five enabled)

```
🧭 TG Agent Relay — help

Relay-handled (zero model tokens):
  /dashboard — dashboard (zero model tokens)
  /stats — stats (zero model tokens)
  /uptime — uptime (zero model tokens)
  /help — help (zero model tokens)
  /usage — usage (zero model tokens)

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

## Inbound media (security)

Photo, voice, video, and allowed audio/image documents from
**`ALLOWED_USER_ID` only** are downloaded via Telegram `getFile` (bot token
never logged or emitted on the agent stream). Files land under
`<bridge>/.media/<chat>/<update_id>/` with directory mode **0700** and file
mode **0600**. Size caps and MIME allowlists are configurable via
`relay.toml` `[media]` (`max_image_bytes`, `max_video_bytes`,
`max_audio_bytes`). The poll loop emits one structured line per attachment:

`[telegram:media] kind=photo path=/abs/... mime=image/jpeg size=N caption=...`

Never commit `.env` (bot token) or `.media/` contents.

## See also

Commands are the inbound (phone → agent) side. For the outbound side —
which Claude Code hook events ping your phone, enabling more of them, and
customizing their message text — see
[`USAGE.md`'s "Sending status" section](USAGE.md#sending-status-outbound-phone---agent)
and the repo README's
["Installing hooks" section](../README.md#installing-hooks-for-more-events).

Every relay-handled command's reply above (`/stats`, `/help`, `/uptime`,
and the text-fallback path of `/dashboard`) is plain text built by its
handler, which then goes through the same structured-formatting layer as
everything else (`## `/leading-emoji headers, code fences, quotes —
`relay.toml`'s `[format]` table) before it reaches your phone — see
[`USAGE.md`'s "Structured formatting" section](USAGE.md#structured-formatting-outbound-messages).

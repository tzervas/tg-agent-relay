# How to use TG Agent Relay

Day-to-day workflows once you're [set up](../SETUP.md). See the
[README](../README.md) for the architecture overview and
[`COMMANDS.md`](COMMANDS.md) for the built-in commands.

## Sending status (outbound, phone <- agent)

Three ways, all funneling through [`tg-send.sh`](../tg-send.sh)'s
pagination/dedup, and all zero model tokens (none of this involves the
model — it's a plain shell script calling `curl`):

### 1. Automatically, via Claude Code hooks

Nothing to do — already wired. `SubagentStop` and `Notification` hook
events fire `hook-notify.sh` → `adapters/claude-code.sh`, which builds a
one-line summary per event type and sends it. Other hook events
(`Stop`, `SubagentStart`, `PreToolUse`/`PostToolUse[Failure]`,
`SessionStart`/`SessionEnd`, `PreCompact`, `StopFailure`) are understood
by the adapter too but only fire if you add their own entry to
`~/.claude/settings.json` pointing at the same `hook-notify.sh` — see
`adapters/claude-code.sh`'s header for the full field reference per
event, and `relay.toml.example`'s `[claude_code.<Event>]` tables to
enable/disable or re-prefix one.

### 2. Directly, from any script/agent

```bash
# raw text — sent exactly as given (after the huge-message cap)
~/.claude/telegram-bridge/relay-notify.sh "Deploy finished OK"

# from stdin
echo "Deploy finished OK" | ~/.claude/telegram-bridge/relay-notify.sh

# structured — renders "<label>: <text>" (plus an optional [generic].prefix)
~/.claude/telegram-bridge/relay-notify.sh --label "deploy" "finished OK"
echo '{"label":"deploy","text":"finished OK"}' \
  | ~/.claude/telegram-bridge/relay-notify.sh

# --raw bypasses ALL formatting — what an adapter that already built its
# own summary string (with its own emoji/prefix baked in) should use
~/.claude/telegram-bridge/relay-notify.sh --raw "✅ my own summary line"
```

A message over `page_size` chars (default 3500, configurable via
`relay.toml`'s `[general].page_size` or `TG_PAGE_SIZE`) is automatically
split on line/paragraph boundaries into multiple `[k/n]`-prefixed
messages, sent in order with a short delay between them so Telegram
preserves ordering.

### 3. Via a custom adapter

If your harness emits structured events (JSON, a specific log line
shape, ...) worth parsing into readable per-event messages the way
`adapters/claude-code.sh` does, write a dedicated adapter — see
[`adapters/README.md`](../adapters/README.md) and copy
[`adapters/generic-example.sh`](../adapters/generic-example.sh) as a
starting point.

## Receiving messages (inbound, phone -> agent)

`tg-poll.sh` long-polls Telegram's `getUpdates` and is meant to run as
your agent's `Monitor`-style event source. It only ever emits a line for
messages from the sender whose numeric id matches `ALLOWED_USER_ID` —
everyone else is silently ignored.

### Reassembly

If you send several messages in quick succession (or Telegram splits one
long message into parts), `tg-poll.sh` buffers them and emits **one**
combined event once `reassemble_window` seconds (default 4, configurable
via `relay.toml`'s `[general].reassemble_window` or
`TG_REASSEMBLE_WINDOW`) pass with no new message:

```
[telegram] first part ⏎ second part ⏎ third part
```

Each buffered message's own internal newlines are flattened to spaces
first, so the combined emission is always exactly one physical stdout
line (the event-stream contract: one line = one notification).

### Plain messages

A message that doesn't match any configured command emits, unchanged:

```
[telegram] can you check the CI run on PR 42?
```

for the agent/`Monitor` to treat as its next input. This is the only
inbound path that costs a model turn.

## Running commands

A message starting with a configured `slash` (`/status`) or `keyword`
(`status ...`) form from `relay.toml`'s `[commands.<name>]` tables is
recognized as a command and routed one of two ways:

- **`mode = "forward"`** (the default if `mode` is omitted) — tagged and
  emitted to the agent:

  ```
  [telegram:cmd:status] status: how's it going?
  ```

  Your agent/hook logic decides what to do with the tag (e.g. a
  `Monitor` source's prompt noticing `[telegram:cmd:status]` and running
  an actual status check).

- **`mode = "relay"`** — the relay itself runs a local `handlers/*.sh`
  script and replies directly; **nothing is emitted to the agent** —
  zero model tokens. This is how `/dashboard`, `/stats`, `/uptime`, and
  `/help` work out of the box. See [`COMMANDS.md`](COMMANDS.md) for the
  full list and how to define your own.

**With no `relay.toml` (or no `[commands.*]` section at all), no message
is ever tagged or relay-handled** — everything emits as plain
`[telegram] <text>`, the original behavior.

## Reading the dashboard

`/dashboard` (relay-handled, zero model tokens) renders a multi-panel
image — header stats, message volume over time, hook-event breakdown by
type, and command usage — over your `.metrics.log` (populated
automatically by `emit_metric` calls throughout the scripts: every send,
poll flush, hook firing, command dispatch, and poll error). Add an
optional trailing window override:

```
/dashboard        # last `[dashboard].window_hours` (default 24h)
/dashboard 48      # last 48 hours
```

If `matplotlib`/`python3` aren't available (or rendering fails for any
reason), the same data renders as a text/unicode dashboard instead — see
[`docs/assets/dashboard-example.txt`](assets/dashboard-example.txt) for
what that looks like. `/dashboard` never fails to send *something*.
`/stats` gives the same numbers as plain text only (no image, lighter).

## Common workflows

**"I want a heads-up when a long-running subagent finishes, without
being pinged on every tool call."** Nothing to configure — `SubagentStop`
is enabled by default and `PreToolUse`/`PostToolUse` are disabled by
default (they're opt-in, high-volume). See `relay.toml.example`'s
`[claude_code.*]` tables.

**"I want to check bridge health from my phone."** Enable the four
built-in commands (uncomment their blocks in `relay.toml` — see
[`COMMANDS.md`](COMMANDS.md)) and send `/uptime` or `/stats`.

**"I want a custom in-chat shortcut that still goes through the agent."**
Add a `[commands.<name>]` table with `mode = "forward"` (or omit `mode`)
— see [`COMMANDS.md`](COMMANDS.md#defining-your-own-command).

**"I want to push status from a non-Claude-Code script (a cron job, a CI
step, another agent)."** Call `relay-notify.sh` directly — see
[Sending status, way 2](#2-directly-from-any-scriptagent) above. No
adapter, no config file, required.

**"I'm getting pinged from a different repo's session."** Expected —
see [SETUP.md's global-hooks caveat](../SETUP.md#caveat-the-claude-code-hooks-are-global).

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
one-line summary per event type and sends it. All **30 documented Claude
Code hook events** are understood by the adapter (see
`adapters/claude-code.sh`'s header for the full per-event field
reference), but only fire if `~/.claude/settings.json` has a matching
`hooks.<Event>` entry pointing at `hook-notify.sh`.

To wire up more events (or fewer), don't hand-edit `settings.json`:

1. Enable/disable events in `relay.toml`'s `[claude_code.<Event>]` tables
   (`enabled = true`/`false`; see `relay.toml.example` for each event's
   install-time default — five low-volume lifecycle events default on,
   the rest are opt-in).
2. Run `~/.claude/telegram-bridge/install-hooks.sh` to sync
   `~/.claude/settings.json` to match. It is **idempotent and
   merge-not-clobber** — it only ever adds/updates/removes the ONE hook
   entry per event that belongs to this bridge (matched by its exact
   `hook-notify.sh` command path); every other key in `settings.json`,
   and any other tool's hook entry for the same event, is left alone.
   `install-hooks.sh --dry-run` previews the plan without writing
   anything; `install-hooks.sh --uninstall` removes every hook entry this
   script ever added.

Each event's DEFAULT message text can also be fully replaced with a
`format = "{placeholder}..."` template in its `[claude_code.<Event>]`
table — see [Message templates](#message-templates) below.

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

### Message templates

Beyond enabling/disabling an event and picking its leading emoji
(`prefix`), you can replace an event's ENTIRE message text with a
`format` string using `{placeholder}` interpolation:

```toml
[claude_code.SubagentStop]
format = "{prefix} {agent} done: {message}"

[generic]
format = "[{label}] {text}"
```

- `[claude_code.<Event>].format` — Claude Code hook events, read by
  `adapters/claude-code.sh`. Every event supports `{prefix}` and
  `{event}` at minimum; see `relay.toml.example`'s per-event comments for
  the rest (`{agent}`, `{tool}`, `{message}`, ...).
- `[generic].format` — the harness-agnostic path (`relay-notify.sh`'s
  structured mode, and any adapter that calls it with `--label` instead
  of building its own text). Placeholders: `{prefix}` `{label}` `{text}`.
  Has no effect under `--raw` (that path always sends its already-built
  text unmodified).

**With no `format` configured (the default), every event renders its
original built-in message text, unchanged** — this is purely additive; an
existing `relay.toml` with no `format` keys behaves exactly as it did
before this existed. A placeholder your template references that the
event doesn't provide is left **literal** in the sent message (e.g. a
typo'd `{mesage}` shows up as-is) rather than silently rendering as a
blank — the same never-silent posture as the rest of this bridge.

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
  zero model tokens. This is how `/dashboard`, `/stats`, `/uptime`,
  `/help`, and `/usage` work out of the box. See [`COMMANDS.md`](COMMANDS.md)
  for the full list and how to define your own.

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

## Token usage dashboard

**OPT-IN — disabled by default.** `/usage` (and, when enabled, extra
panels appended to `/dashboard` too) renders a token USAGE breakdown —
by **provider** (Anthropic/OpenAI/Google/other, inferred from the model
id), by **model**, and by **project** — over your local coding-agent
session history. It's a separate, independent aggregation from
`/dashboard`'s relay-operational metrics (`.metrics.log`): this one reads
transcript files a *harness* writes, not this relay.

### Enabling it

Nothing runs until you opt in in `relay.toml`:

```toml
[usage]
enabled = true
```

Uncomment `[commands.usage]` too (see
[`COMMANDS.md`'s `/usage` entry](COMMANDS.md#the-five-built-in-relay-handled-commands))
to get the `/usage` command; `/dashboard` picks up the usage panels
automatically once `[usage].enabled = true`, no separate toggle needed.

### The source adapter

`[usage].source` selects which adapter reads `[usage].projects_dir`. **One
ships today: `"claude-code"`** (the default) — it walks Claude Code's own
on-disk session-transcript layout, `~/.claude/projects/<project>/*.jsonl`
(the default `projects_dir`), reading each assistant message's `usage`
object (`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens`) and its `model` id. This relay talks to any
harness (see the README's architecture overview) — a future harness gets
its own adapter in `lib/usage_ingest.py` without any caller changing;
point `projects_dir` at wherever that harness's compatible transcripts
live. An unrecognized `source` value skip-gracefully disables collection
(a clear "unknown usage source adapter" note in the reply) rather than
guessing or erroring.

**Best-effort, "where available":** a missing `projects_dir`, an empty
history, or a malformed transcript line never errors and never fabricates
a number — the dashboard/command just shows an honest empty or partial
result, optionally with a short note explaining what was skipped.

### Reading it

```
/usage             # relay.toml's [usage].window (default 7d)
/usage today        # since local midnight
/usage 30d          # last 30 days
/usage all          # everything the source has
```

The image shows header stat tiles (total tokens, input/output split, top
model, top project), a **tokens by model** bar, a **tokens by provider**
share bar (percentages — deliberately a bar, not a pie: see the
`/dataviz` design skill's guidance on comparing a handful of categories),
a **tokens by project** bar, and — when there's enough spread of
timestamps to be meaningful — a small over-time trend line. `[usage].providers`
/`[usage].models` (both default `true`) can turn off either breakdown; the
panel still renders in the same spot, labeled "(display disabled)" rather
than silently vanishing. With `[usage].enabled = false` (the default),
`/dashboard` renders **exactly** as it always has — no usage panels, no
behavior change at all.

### Privacy — read this before enabling

**Token usage is personal data, and this relay/its source repo is
typically public.** By design:

- **Local-only, never transmitted anywhere but your own chat.** The
  transcript files this reads live entirely on your machine
  (`~/.claude/projects/` by default); the aggregated summary this writes
  is a **local cache file** (`.usage/usage-summary.json`, under this
  bridge's own directory); the rendered image/text is sent **only** to
  the Telegram chat ID already allowlisted in your `.env` — this feature
  makes no other network call, ever.
- **Gitignored by construction.** `.gitignore` excludes `.usage/`,
  `*.usage.json`, and `usage-cache/*` — the cache this feature writes can
  never be accidentally committed. If you fork/customize this repo,
  double-check `git status` shows nothing under those paths as trackable
  before you push.
- **Never enabled by accident.** `[usage].enabled` defaults to `false`;
  with it unset, `/usage` replies that tracking is disabled (rather than
  silently doing nothing), and `/dashboard` is byte-identical to before
  this feature existed.
- **Test fixtures are synthetic.** `tests/fixtures/usage-synthetic/` uses
  fabricated project names, model ids, and token counts only — never real
  usage data, by policy.

## Common workflows

**"I want a heads-up when a long-running subagent finishes, without
being pinged on every tool call."** Nothing to configure — `SubagentStop`
is enabled by default and `PreToolUse`/`PostToolUse` are disabled by
default (they're opt-in, high-volume). See `relay.toml.example`'s
`[claude_code.*]` tables.

**"I want to check bridge health from my phone."** Enable the built-in
commands (uncomment their blocks in `relay.toml` — see
[`COMMANDS.md`](COMMANDS.md)) and send `/uptime` or `/stats`.

**"I want to see where my token spend is going."** Enable `[usage]` and
`/usage` (see "Token usage dashboard" above) and send `/usage 7d`.

**"I want a custom in-chat shortcut that still goes through the agent."**
Add a `[commands.<name>]` table with `mode = "forward"` (or omit `mode`)
— see [`COMMANDS.md`](COMMANDS.md#defining-your-own-command).

**"I want to push status from a non-Claude-Code script (a cron job, a CI
step, another agent)."** Call `relay-notify.sh` directly — see
[Sending status, way 2](#2-directly-from-any-scriptagent) above. No
adapter, no config file, required.

**"I'm getting pinged from a different repo's session."** Expected —
see [SETUP.md's global-hooks caveat](../SETUP.md#caveat-the-claude-code-hooks-are-global).

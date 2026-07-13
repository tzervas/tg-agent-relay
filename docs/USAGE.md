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

## Structured formatting (outbound messages)

Every message text built above (a hook summary, a `relay-notify.sh` call,
a handler's reply) is run through [`lib/format.sh`](../lib/format.sh)
before `tg-send.sh` posts it — a **v0.3.0 headline feature, on by
default** — turning a wall of text into a message with real visual
hierarchy on the phone. Telegram has no true font sizes, so this uses
what it actually supports: `parse_mode=HTML` (only `<`/`>`/`&` need
escaping — far safer than MarkdownV2's ~18-character escape table).

### Before / after

The exact same report, before v0.3.0 vs. with structured formatting:

**Before** (v0.2.x — a wall of text, plain `sendMessage`):

```text
Findings: found the issue in format_message() in swap.myc -- it wasn't
validating a swap before applying it. before: fn swap(v: Value) -> Value
{ v.as_dense() } after: fn swap(v: Value) -> Result<Value, SwapError> {
v.as_dense().ok_or(SwapError::OutOfRange) } note: an out-of-range swap is
now an explicit Result, not a silent truncation. all tests green
(165/165)
```

**After** (v0.3.0 — the SAME content, using the input-markup convention
below), rendered on the phone as: a bolded **Findings** header, an inline
`format_message()`/`swap.myc` code reference, a real monospace **code
box** for the `myc` diff (verbatim, never reflowed), an (optionally
expandable) quoted note, and a bolded closing line:

    ## Findings

    Found the issue in `format_message()` in `swap.myc` — it wasn't
    validating a swap before applying it.

    ```myc
    // before
    fn swap(v: Value) -> Value {
        v.as_dense()
    }

    // after
    fn swap(v: Value) -> Result<Value, SwapError> {
        v.as_dense().ok_or(SwapError::OutOfRange)
    }
    ```

    > Never-silent: an out-of-range swap is now an explicit `Result`,
    > not a silent truncation.

    ✅ TESTS GREEN (165/165)

### The input-markup convention

Your plain message text drives the rendering — no HTML to write by hand:

- `## Header` -> `<b>Header</b>` (bold, blank line above).
- A line with a **leading emoji + a SHORT all-caps or Title-Case phrase**
  (<=60 chars), e.g. `✅ BUILD FINISHED` or `🚀 Deploy Started` -> also
  bolded. An ordinary leading-emoji SENTENCE with any lowercase,
  non-Title-Case word (e.g. `✅ build finished — 2 issues found`) stays
  plain prose — conservative by design, so this never mis-fires on a
  normal status ping.
- ` ```lang ... ``` ` (a fenced code block) -> a real Telegram code box
  (`<pre><code class="language-lang">...</code></pre>`). The content is
  **NEVER reflowed, wrapped, or marked up** — only the three HTML-special
  characters are escaped, byte-for-byte verbatim otherwise. Common tags
  pass through as-is: `rust`, `python`/`py`, `bash`/`sh`, `json`, `yaml`,
  `toml`, `c`/`cpp`, `go`, `js`/`ts`, `java`, `sql`, `diff`, `html`,
  `css`, `ruby`, `php`, and more (see `lib/format.sh`'s `_fmt_known_lang`
  for the full list). **`myc` and `mycelium` are first-class tags** —
  both normalize to `language-mycelium`, since Mycelium is this
  ecosystem's own language. An unrecognized tag still boxes the code,
  just without a language class (a plain `<pre>`).
- `` `inline code` `` -> `<code>...</code>` (monospace, never reflowed —
  an inline code span with embedded spaces is kept whole by the
  soft-wrap pass too, never torn mid-span).
- `> quoted line(s)` -> consecutive `> `-prefixed lines group into ONE
  `<blockquote>...</blockquote>`; a long quote (more than 3 lines, or
  over 200 characters) automatically becomes an **expandable**
  blockquote (a native Telegram feature — collapsed by default on the
  phone, tap to expand).
- `*emphasis*` / `_emphasis_` -> `<i>...</i>`. Word-boundary-guarded: the
  character immediately outside each marker must not be alphanumeric, so
  a snake_case identifier like `my_var_name` (outside backticks) is never
  mistaken for emphasis — the classic Markdown-in-prose false positive.

### Dynamic soft-wrap

A prose line longer than `wrap_width` (default 50 — a phone-friendly
width) is wrapped at **word boundaries**. It never breaks a word, a URL,
or the inside of an inline `` `code span` `` (even one with embedded
spaces — protected as a single atomic token). A line already within
`wrap_width` is left completely untouched. Fenced code-block interiors
are never wrapped, regardless of width (see above — always verbatim).

### Config — `relay.toml`'s `[format]` table

```toml
[format]
enabled = true          # master switch
parse_mode = "HTML"     # "HTML" (default, implemented) | "MarkdownV2"
                         # (accepted but not yet rendered - falls back to
                         # plain text, logged) | "none" (= enabled=false)
wrap_width = 50          # soft-wrap width in characters
headers = true            # "## Header" / leading-emoji CAPS-or-Title lines
code_spans = true         # `inline code` and ```lang fences
blockquotes = true        # "> quoted" lines
soft_wrap = true          # the word-boundary wrap pass
```

**`enabled = false` (or `parse_mode = "none"`) restores today's exact
pre-v0.3.0 plain-text behavior, byte-for-byte** — this is the one
opt-out that matters if you'd rather receive raw, unformatted text; every
other toggle above defaults on and can be flipped off individually
without disabling the whole layer. This is also the **one exception** to
"no `relay.toml` -> byte-identical to before the feature existed" that
this bridge otherwise guarantees everywhere else — structured formatting
is meant to just work out of the box.

### Never-silent (a message is never dropped nor sent broken)

`format_message()` self-checks the HTML it renders (an open/close tag
balance check) before using it; if that check ever fails, it falls back
to the plain, HTML-escaped original text and logs the downgrade. On top
of that, `tg-send.sh` itself checks Telegram's actual API response: if a
formatted send is rejected (an HTML-parse error on Telegram's side), it
retries **once** as plain text and logs that fallback too. Either way,
the message still arrives — formatting is a readability improvement, not
a new way for a status ping to silently disappear.

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

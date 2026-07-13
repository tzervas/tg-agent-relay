# TG Agent Relay

**An agent/harness-agnostic Telegram relay:** full-output, paginated status
pings go out to your phone for free; reassembled messages and commands come
back in; and a set of built-in dashboard/stats commands answer straight from
the relay — zero model tokens either direction unless *you* start a
conversation.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![gitleaks](https://github.com/tzervas/tg-agent-relay/actions/workflows/gitleaks.yml/badge.svg)](https://github.com/tzervas/tg-agent-relay/actions/workflows/gitleaks.yml)

Built with pure `curl` + `jq` + `python3` stdlib — no framework, no external
services, no listening port.

> **Repo/directory note:** this repo is named `tg-agent-relay` on GitHub
> (renamed from `claude-telegram-bridge` — GitHub auto-redirects the old
> URL). The **local working copy stays at `~/.claude/telegram-bridge/`** —
> that path is baked into a live Claude Code hook config and other agents'
> script invocations, so it's left unchanged deliberately. The name
> mismatch (repo `tg-agent-relay`, directory `telegram-bridge`) is
> cosmetic only.

## Why

Two channels, deliberately split so **status pings cost zero model
tokens** and you are only billed when *you* message the bot:

- **Outbound status → phone (0 model tokens):** any agent/harness calls
  [`relay-notify.sh`](relay-notify.sh) (generic) or a harness-specific
  [adapter](adapters/) → `tg-send.sh` (auto-paginating `[k/n]` over
  Telegram's 4096-char cap) → Telegram. Hook-driven pings run outside the
  model, so automated status never spends tokens.
- **Inbound phone → agent (billed only here):** `tg-poll.sh` long-polls
  `getUpdates`, **allowlisted strictly to your numeric `ALLOWED_USER_ID`**
  (every other sender is silently ignored — the security boundary),
  reassembles a rapid burst of messages into one event, and either
  forwards it to the agent or — for a small set of built-in commands —
  answers it itself, at zero model tokens.

## Architecture

```mermaid
flowchart LR
    subgraph agent["Agent / harness (any)"]
        CC["Claude Code hook\n(SubagentStop, Notification, ...)"]
        ANY["Any script or agent"]
    end

    subgraph relay["TG Agent Relay (~/.claude/telegram-bridge/)"]
        HN["hook-notify.sh\n(shim)"]
        AD["adapters/claude-code.sh"]
        RN["relay-notify.sh\n(generic core)"]
        TS["tg-send.sh\n(paginate k-of-n, dedup)"]
        TP["tg-poll.sh\n(reassembly + command parser)"]
        DISP{"command matched?"}
        HD["handlers/*.sh\n(dashboard, stats, uptime, help, usage)"]
    end

    PHONE(["Your phone (Telegram)"])

    CC -->|hook JSON on stdin| HN --> AD -->|raw formatted text| RN
    ANY -->|"free text, or label:text"| RN
    RN --> TS -->|sendMessage, paginated| PHONE

    PHONE -->|message| TP
    TP --> DISP
    DISP -->|"relay-handled\nmode is relay"| HD
    HD -->|"sendPhoto / sendMessage\nzero model tokens"| PHONE
    DISP -->|"no match, or\nmode is forward"| OUT["tagged telegram event\non stdout"]
    OUT -->|stdout event| AGENT_IN["Agent / Monitor\n(billed only here)"]
```

- **Outbound (top path):** agent/hook → adapter or `relay-notify.sh` →
  `tg-send.sh` → Telegram → your phone. Never costs a model turn.
- **Inbound (bottom path):** phone → Telegram → `tg-poll.sh`. A flushed
  message is either **relay-handled** (a built-in command like
  `/dashboard` runs a local script and replies via `sendPhoto`/
  `sendMessage` — zero model tokens) or **forwarded** as a
  `[telegram] ...` / `[telegram:cmd:<tag>] ...` line on stdout for your
  agent's event source (a `Monitor`-style loop) to read — costing a model
  turn only when you actually send something.

## The dashboard, at a glance

`/dashboard` renders a dark-friendly, mobile-legible multi-panel PNG
(header stats, volume-over-time, hook-event breakdown, command usage) via
`sendPhoto` — no model involved. Illustrative example (synthetic data —
see [`docs/assets/README.md`](docs/assets/README.md)):

![dashboard example](docs/assets/dashboard-example.png)

If `matplotlib`/`python3` aren't available, the same data renders as a
unicode/text dashboard instead — see
[`docs/assets/dashboard-example.txt`](docs/assets/dashboard-example.txt).
Either way, `/dashboard` never fails to send *something*.

## Token usage dashboard (opt-in)

**Disabled by default.** `/usage` renders tokens by **provider**
(Anthropic/OpenAI/Google/other), **model**, and **project**, aggregated
from a harness's local session-transcript logs (one adapter ships today —
Claude Code's own `~/.claude/projects/**/*.jsonl`). Illustrative example
(synthetic fixture data — see [`docs/assets/README.md`](docs/assets/README.md)):

![usage example](docs/assets/usage-example.png)

**Privacy first, read before enabling:** everything stays local
(transcripts are never touched outside your machine, the aggregate cache
is gitignored) and the render goes only to your own allowlisted Telegram
chat — never any other network call. See
[`docs/USAGE.md`'s "Token usage dashboard" section](docs/USAGE.md#token-usage-dashboard)
for the full opt-in config and privacy note before turning `[usage]` on.

## Quickstart

```bash
# 1. Create a bot via @BotFather in Telegram, copy the token
# 2. Put it in a local, gitignored .env
cp .env.example .env
echo 'BOT_TOKEN=<paste-your-token-here>' >> .env   # or edit .env directly
chmod 600 .env

# 3. Message your new bot once (any text), so the relay can learn your id
# 4. Go live — validates the token, resolves your id, sends a confirmation DM
bash go-live.sh
```

Full walkthrough (including optional `relay.toml` config and wiring an
adapter): see [`SETUP.md`](SETUP.md).

## In use

### (a) Wiring to Claude Code

Already wired for you: `~/.claude/settings.json`'s `hooks.SubagentStop` /
`hooks.Notification` call `hook-notify.sh`, a thin shim that `exec`s
[`adapters/claude-code.sh`](adapters/claude-code.sh) — which parses the
hook's JSON payload, builds a one-line summary per event type, and hands
it to `relay-notify.sh --raw`:

```json
{
  "hooks": {
    "SubagentStop": [{ "hooks": [{ "type": "command", "command": "~/.claude/telegram-bridge/hook-notify.sh" }] }],
    "Notification": [{ "hooks": [{ "type": "command", "command": "~/.claude/telegram-bridge/hook-notify.sh" }] }]
  }
}
```

`adapters/claude-code.sh` understands **all 30 documented Claude Code hook
events**, not just these two — see [Installing hooks](#installing-hooks-for-more-events)
below to wire any of the rest.

### (b) Wiring to ANY other agent/harness

No adapter needed for plain text — call the generic entry point directly:

```bash
# raw text
~/.claude/telegram-bridge/relay-notify.sh "Deploy finished OK"

# structured (adds an optional [generic].prefix from relay.toml)
echo '{"label":"deploy","text":"finished OK"}' \
  | ~/.claude/telegram-bridge/relay-notify.sh
```

If your harness has its own structured event shape worth parsing (like
Claude Code's hook JSON), copy
[`adapters/generic-example.sh`](adapters/generic-example.sh) and write a
dedicated adapter — see [`adapters/README.md`](adapters/README.md).

### (c) A status ping arriving on the phone

A `SubagentStop` hook firing produces a DM like:

```
✅ code-reviewer finished — Found 2 issues in the diff, both low severity
```

A long message (e.g. a full tool output) is auto-paginated, with a
**bolded** `[k/n]` page header (see "Structured formatting" below):

```
[1/3]
✅ build finished — Compiling mycelium-core v0.4.0 ...
```

**Structured formatting (v0.3.0 — on by default):** a longer, richer
report — the kind of message that used to arrive as an unreadable wall of
text — renders with real hierarchy instead. Before/after, the same
message:

<table>
<tr><th>Before (v0.2.x — a wall of text)</th><th>After (v0.3.0 — structured)</th></tr>
<tr><td>

```text
Findings: found the issue in format_message()
in swap.myc -- it wasn't validating a swap
before applying it. before: fn swap(v: Value)
-> Value { v.as_dense() } after: fn swap(v:
Value) -> Result<Value, SwapError> {
v.as_dense().ok_or(SwapError::OutOfRange) }
note: an out-of-range swap is now an explicit
Result, not a silent truncation. all tests
green (165/165)
```

</td><td>

**Findings**

Found the issue in `format_message()` in
`swap.myc` — it wasn't validating a swap
before applying it.

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

> Never-silent: an out-of-range swap is now an
> explicit `Result`, not a silent truncation.

**✅ TESTS GREEN (165/165)**

</td></tr>
</table>

The right-hand column is exactly what arrives on the phone — a bolded
header, a monospace `inline code` reference, a real **code box** for the
`myc` diff (Telegram renders `mycelium`/`myc` fences as a monospace box,
byte-for-byte verbatim — never reflowed or re-marked-up), a quoted note,
and a bolded closing line — instead of one dense paragraph. See
"Structured formatting" below for the full input-markup convention and
config, and [`docs/USAGE.md`](docs/USAGE.md#structured-formatting-outbound-messages)
for more examples.

### (d) Sending `/dashboard` → image back

```
You:  /dashboard
Bot:  [sends a PNG: Relay Dashboard — last 24h]
```

Zero model tokens — `tg-poll.sh` matches the command, backgrounds
`handlers/dashboard.sh`, which renders and sends the image (or the
text-fallback dashboard) directly. See
[`docs/COMMANDS.md`](docs/COMMANDS.md) for the full command table.

### (e) A plain message → forwarded to the agent

```
You:  can you check the CI run on PR 42?
```

`tg-poll.sh` sees no command match and emits, on its own stdout:

```
[telegram] can you check the CI run on PR 42?
```

for your agent's `Monitor`-style event loop to pick up as its next input —
this is the only inbound path that costs a model turn.

### Installing hooks for more events

`adapters/claude-code.sh` handles all 30 documented Claude Code hook
events, but only the two live in `~/.claude/settings.json` today
(`SubagentStop`, `Notification`) actually fire — Claude Code only invokes a
hook if `settings.json` says to. To wire up more (or fewer), don't hand-edit
`settings.json`: enable/disable events in `relay.toml`, then run

```bash
~/.claude/telegram-bridge/install-hooks.sh          # sync settings.json to relay.toml
~/.claude/telegram-bridge/install-hooks.sh --dry-run  # preview the plan, change nothing
~/.claude/telegram-bridge/install-hooks.sh --uninstall # remove every relay-added hook entry
```

It reads each event's `[claude_code.<Event>].enabled` from `relay.toml`
(falling back to that event's own install-time default — five events
default on, the rest are opt-in; see `relay.toml.example`), and
reconciles `hooks.<Event>` in `~/.claude/settings.json` accordingly.
**Idempotent and merge-not-clobber**: it only ever touches the ONE hook
entry it owns per event (identified by its own `hook-notify.sh` command
path) — every other key in `settings.json`, and every other tool's hook
entry for the same event, is left exactly as it was. Re-running it after
editing `relay.toml` is the normal workflow; running it with no change is
a reported no-op. It never writes an invalid `settings.json` — the result
is JSON-validated before AND after the write, and a `settings.json` that
already fails to parse is left untouched with a nonzero exit rather than
guessed at.

## Configurable via `relay.toml` (optional)

Copy [`relay.toml.example`](relay.toml.example) to `relay.toml` to
configure page size/delay, the reassemble window, structured message
formatting, which Claude Code hook events are enabled + their prefix +
their message format, the `[generic]` prefix/format, in-chat commands,
the dashboard window, optional local TTS voice notes, and the opt-in
token-usage dashboard. **Every script falls back to its pre-existing
env-var/hardcoded default with no `relay.toml` present** — this is the
backward-compat guarantee: an existing bridge with no `relay.toml`
behaves byte-for-byte as it always has — **with one deliberate exception,
`[format]` below**, which is ON by default. See the example file's
comments for the full schema, and [`docs/USAGE.md`](docs/USAGE.md) /
[`docs/COMMANDS.md`](docs/COMMANDS.md) for how to use it.

### Structured formatting — phone-readable messages (v0.3.0, on by default)

`tg-send.sh` runs every outbound message through
[`lib/format.sh`](lib/format.sh) before it hits Telegram's API, turning a
wall of text into a message with real visual hierarchy — using the
formatting Telegram actually supports (`parse_mode=HTML`: dynamic
soft-wrap at word boundaries, bolded section headers, real code boxes,
expandable quotes, light emphasis), since Telegram has no true font
sizes. See "(c) A status ping arriving on the phone" above for a full
before/after example.

**Your plain message text drives it** — a small, documented input-markup
convention:

| Write this | Get this |
|---|---|
| `## Header` | **Header** (bold, blank line above) |
| `✅ SHORT CAPS` or `🚀 Title Case` (leading emoji + short all-caps/Title-Case) | **bolded header** — an ordinary lowercase sentence stays plain prose, never mistakenly bolded |
| ` ```lang ... ``` ` | a real code box (`<pre><code>`) — content is **never** reflowed, wrapped, or marked up, only HTML-escaped, byte-for-byte verbatim |
| `` `inline code` `` | monospace `inline code` |
| `> quoted line(s)` | a blockquote (auto-**expandable** if long) |
| `*emphasis*` / `_emphasis_` | *italic* — word-boundary-guarded, so `my_var_name` outside backticks is never mistaken for emphasis |

**Code fences recognize the common language tags** (`rust`, `python`,
`bash`, `json`, `yaml`, `toml`, `go`, `js`, `ts`, `java`, `sql`, `diff`,
...) — and **`myc`/`mycelium` are first-class tags** (both normalize to
`language-mycelium`), since Mycelium is this ecosystem's own language. An
unrecognized tag still boxes the code, just without a language class.

Config — `relay.toml`'s `[format]` table, every key optional and on by
default:

```toml
[format]
enabled = true
parse_mode = "HTML"   # "HTML" (default) | "MarkdownV2" (not yet rendered,
                       # logged + falls back to plain text) | "none"
wrap_width = 50        # soft-wrap width, phone-friendly default
headers = true
code_spans = true
blockquotes = true
soft_wrap = true
```

`enabled = false` (or `parse_mode = "none"`) restores today's exact
plain-text behavior, byte-for-byte — the one opt-out you need if you'd
rather keep receiving raw text. **Never-silent:** a render that would
produce malformed HTML, or a Telegram-side HTML-parse rejection, retries
ONCE as plain text and logs the fallback via `.metrics.log` — a message
is never dropped nor sent with broken markup. See
[`docs/USAGE.md`](docs/USAGE.md#structured-formatting-outbound-messages)
for the full writeup and more examples.

### Voice messages (TTS) — self-hosted, off by default

`tg-send.sh` can also generate a **voice note** locally from a message's
text and send it via Telegram's `sendVoice` — **no external TTS API**;
piper or espeak-ng run entirely on this machine. Opt in via `relay.toml`'s
`[tts]` table (`mode = "text+voice"` sends text then voice; `"voice-only"`
sends only voice, falling back to text if TTS is unavailable — a message
is never dropped). Default `mode = "off"` means the feature is inert with
no `relay.toml`, or an existing one that doesn't set it — identical
behavior to before TTS existed.

**Recommended: piper + a medium/high voice model** for natural-sounding
speech (espeak-ng is the zero-config fallback — always available, but
robotic). One-liner: `./fetch-voices.sh` fetches the recommended deep-male
voice (`en_US-joe-medium`) into `./voices/`; `./fetch-voices.sh --list`
shows every recommended voice (male + female). Then:

```toml
[tts]
mode = "text+voice"
engine = "piper"
voice_model = "/root/.claude/telegram-bridge/voices/en_US-joe-medium.onnx"
length_scale = "0.81"   # optional cadence tweak — see below; this is the
                          # tuned, approved-final default for this voice
max_chars = 600
```

See `relay.toml.example`'s `[tts]` comments for the full schema (including
the optional `pitch` depth knob and `length_scale` cadence knob, piper's
own `--length-scale` — lower is faster; `"0.81"` above is this bridge's
recommended cadence, with `pitch` left off) and
[`SETUP.md`](SETUP.md#voice-messages-tts-optional) for installing piper
or espeak-ng.

### Per-event message templates

Beyond `enabled`/`prefix`, every `[claude_code.<Event>]` table (and
`[generic]`, for the harness-agnostic path) accepts a `format` string with
`{placeholder}` interpolation, e.g.:

```toml
[claude_code.SubagentStop]
format = "{prefix} {agent} done: {message}"
```

With no `format` set, an event renders its original built-in default text
— unchanged. See `relay.toml.example`'s per-event comments for each
event's available placeholders, and `adapters/claude-code.sh`'s header for
the full per-event field reference.

## In-chat commands (user → agent)

`tg-poll.sh` recognizes a leading `/slash` command or a configured
keyword prefix (`status ...`, `pause ...`) defined in `relay.toml`'s
`[commands.<name>]` tables, and tags the emitted event:

```
[telegram:cmd:status] status: how's it going?      # matched a command
[telegram] just a regular message                    # no match (unchanged)
```

**With no `relay.toml` (or no `[commands.*]` section), nothing is ever
tagged** — every message emits exactly as `[telegram] <text>`, today's
behavior.

A command can also be **relay-handled** (`mode = "relay"` + `handler`)
instead of forwarded — the relay runs a local script and answers
directly, at zero model tokens, instead of ever emitting the event to the
agent. Five such commands ship today (`/dashboard`, `/stats`, `/uptime`,
`/help`, `/usage` — the last opt-in, see "Token usage dashboard" above) —
see [`docs/COMMANDS.md`](docs/COMMANDS.md) for what each does and how to
define your own.

## Files

| File | Purpose |
|---|---|
| `.env.example` | Secret config template (committed). Copy to `.env` and fill `BOT_TOKEN`. |
| `.env` | **Local-only, gitignored, 0600** — holds the live bot token. Never committed. |
| `relay.toml.example` | Non-secret config template (committed). Copy to `relay.toml` to customize. |
| `relay.toml` | **Local-only, gitignored** — your actual config. Optional; scripts fall back gracefully without it. |
| `tg-send.sh` | Outbound `sendMessage`; silent no-op with no token; 10s dedup; auto-paginates (`[k/n]`) over `page_size`/`TG_PAGE_SIZE` (default 3500) chars; structured formatting (`[format]`, default ON); optional local TTS voice note (`[tts]`, default off). |
| `lib/format.sh` | Structured-formatting layer (`[format]`, v0.3.0, on by default) — dynamic soft-wrap, bolded headers, code boxes (`myc`/`mycelium` first-class), quotes, emphasis, via `parse_mode=HTML`; never-silent fallback to plain text on a bad render or a Telegram-side rejection. |
| `lib/tts.sh` | Self-hosted TTS pipeline (text → WAV via piper/espeak-ng → OGG/OPUS via ffmpeg → `sendVoice`), skip-graceful with no engine/ffmpeg installed. |
| `fetch-voices.sh` | One-command piper voice model downloader (`.onnx` + `.onnx.json`, sha256-verified, skip-graceful); no args fetches the recommended default (`en_US-joe-medium`), `--list` shows the full recommended table. |
| `tg-poll.sh` | Inbound long-poll; strict id-allowlist; emits `[telegram] <text>` (or `[telegram:cmd:<tag>] <text>` for a recognized command); reassembles a rapid burst into one event after a quiet gap; routes `mode = "relay"` commands to a `handlers/` script instead. |
| `relay-notify.sh` | **Generic, harness-agnostic entry point** — any agent/script can send a status update through it directly. |
| `adapters/claude-code.sh` | Claude Code hook-JSON adapter — parses the payload, formats a per-event summary, calls `relay-notify.sh --raw`. |
| `adapters/generic-example.sh` | Copy-paste stub for writing a new harness adapter. |
| `adapters/README.md` | How to write an adapter for any harness. |
| `hook-notify.sh` | **Backward-compatible shim** — wired into `~/.claude/settings.json`; just `exec`s `adapters/claude-code.sh`. |
| `install-hooks.sh` | One-command, idempotent, merge-not-clobber installer/uninstaller — syncs `~/.claude/settings.json`'s Claude Code hook entries to whatever's `enabled` in `relay.toml`. |
| `lib/relay-config.sh` | Optional `relay.toml` loader (`cfg_get`/`cfg_has_section`), shared by every script. |
| `lib/relay-common.sh` | Shared helpers (`oneline`, `cap_if_huge`, `emit_metric`, `render_template`). |
| `lib/claude-code-events.sh` | Single source of truth for the documented Claude Code hook event list + each event's install-time default enabled/disabled + default prefix — shared by `adapters/claude-code.sh` and `install-hooks.sh`. |
| `lib/toml_to_json.py` | `relay.toml` → JSON (Python stdlib `tomllib`; the only TOML-parsing code in the repo). |
| `lib/metrics_agg.py` | Pure/stdlib metrics aggregation over `.metrics.log` (used by the dashboard/stats/uptime handlers, unit-tested independently of matplotlib). |
| `lib/dashboard_render.py` | Renders the multi-panel dashboard PNG (matplotlib), degrading to the text renderer on any failure. Also renders the opt-in token-usage panels/image. |
| `lib/usage_ingest.py` | **Opt-in** (`[usage].enabled`), pure/stdlib token-usage aggregation by provider/model/project over a harness's local session-transcript logs — source-adapter abstraction, one adapter ships today (Claude Code). See `docs/USAGE.md`'s "Token usage dashboard" section. |
| `handlers/` | Relay-handled-command scripts for `mode = "relay"` in `relay.toml` — `dashboard.sh`, `stats.sh`, `uptime.sh`, `help.sh`, `usage.sh` ship today; see [`docs/COMMANDS.md`](docs/COMMANDS.md) and [`handlers/README.md`](handlers/README.md). |
| `.metrics.log` | **Local-only, gitignored, auto-created** — a TSV event log (`emit_metric`) that the dashboard/stats/uptime commands read. |
| `.usage/` | **Local-only, gitignored, auto-created, opt-in** — the token-usage aggregate cache `handlers/usage.sh`/`dashboard.sh` write when `[usage].enabled = true`. Never committed; see the privacy note in `docs/USAGE.md`. |
| `go-live.sh` | Validates the token, auto-resolves your id, sends the "🟢 live" DM. |
| `watch-go-live.sh` | Optional: waits for a token to appear, then runs `go-live.sh`. |
| `SETUP.md` | Step-by-step setup + security notes. |
| `docs/USAGE.md` | How to send status, receive/reassemble messages, run commands, and read the dashboard. |
| `docs/COMMANDS.md` | The built-in commands, relay-handled vs. forwarded, and how to define your own. |
| `ROADMAP.md` | Where this is headed. |

## Security

- **Allowlist by numeric `user_id`**; all other senders are silently
  ignored — never forwarded, never logged with content.
- **Bot token lives only in the local `.env`** (gitignored, 0600) — never
  in the repo. `relay.toml` (if you create one) holds no secret either.
- **Outbound-only.** Telegram bots work by your machine *polling out*
  (`getUpdates`) and *pushing out* (`sendMessage`/`sendPhoto`) — no
  inbound port is ever opened on this machine.
- **Secret-scanned:** [gitleaks](https://github.com/gitleaks/gitleaks)
  runs as a pre-commit hook (`.pre-commit-config.yaml`) and a CI check
  (`.github/workflows/gitleaks.yml`) on every push/PR, including a
  repo-specific rule for this bot token's exact shape (`.gitleaks.toml`).

See [`SETUP.md`](SETUP.md#security-model) for the full security model.

## License

MIT — see [`LICENSE`](LICENSE). Copyright (c) 2026 Tyler Zervas.

# TG Agent Relay — setup

A secure, token-frugal bridge between your phone (Telegram) and this Claude
Code session (or, via `relay-notify.sh`/an adapter, any other agent or
harness — see the repo [README](README.md)). Everything lives under
`~/.claude/telegram-bridge/` (mode 0700, kept at this path deliberately —
see the README's repo/directory note) — no other repo is touched.

### Python version & tooling

**Preferred: Python 3.14** (see `.python-version`). Use **uv** for the
project env and **ruff** for lint/format:

```bash
# install uv: https://docs.astral.sh/uv/
bash scripts/dev.sh sync     # uv sync → .venv on 3.14 + ruff/pytest
bash scripts/dev.sh check    # ruff + offline tests
```

Runtime scripts resolve Python via [`lib/python.sh`](lib/python.sh):
project `.venv` → `python3.14` → `python3.13` → `python3` (≥3.11 for
tomllib). Override with `RELAY_PYTHON=…`. Full notes: [`docs/TOOLING.md`](docs/TOOLING.md).

**Rust** (optional crates later): **MSRV 1.96** — `rust-toolchain.toml` +
`Cargo.toml` `rust-version = "1.96"`, with rustfmt, clippy, rust-src,
rust-analyzer (`bash scripts/dev.sh rust-check`).

## How it stays token-frugal

- **Outbound status pings (phone <- agent) cost ZERO model tokens.** They
  fire from hooks/adapters (plain shell script invocations, no model call
  involved) via `tg-send.sh`, a pure `curl` POST to Telegram.
- **Inbound messages (phone -> agent) only cost tokens when you actually
  send one.** `tg-poll.sh` long-polls Telegram for free (no model call); it
  only produces output — and therefore only wakes the session — when you
  send a real message, and only when that message isn't answered directly
  by a **relay-handled command** (see step 5). You are billed exactly for
  the turns you initiate.

## Setup steps

### 1. Create your bot with BotFather

In Telegram, message **@BotFather** → `/newbot` → follow the prompts →
copy the token it gives you (looks like
`123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`).

### 2. Put the token in your local `.env`

```bash
cp ~/.claude/telegram-bridge/.env.example ~/.claude/telegram-bridge/.env
chmod 600 ~/.claude/telegram-bridge/.env
```

Then edit `.env` and replace the empty `BOT_TOKEN=` line with
`BOT_TOKEN=<your token>`:

```bash
# .env
BOT_TOKEN=123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ALLOWED_USER_ID=
ALLOWED_CHAT_ID=
```

Leave `ALLOWED_USER_ID`/`ALLOWED_CHAT_ID` empty — `go-live.sh` (step 4)
resolves and writes them for you. `.env` is gitignored and mode `0600`;
**never commit it**.

### 3. (Optional) Configure `relay.toml`

Everything works with defaults and no `relay.toml` at all. If you want to
change page size, enable more Claude Code hook events, add in-chat
commands, or enable the built-in dashboard commands:

```bash
cp ~/.claude/telegram-bridge/relay.toml.example ~/.claude/telegram-bridge/relay.toml
```

Edit `relay.toml` and uncomment/add only what you want — every value has
a hardcoded/env-var fallback, so an untouched `relay.toml` (or none at
all) changes nothing — **except structured formatting (`[format]`,
v0.3.0), which is ON by default with no `relay.toml` needed at all.**
Your phone will already receive bolded headers, code boxes, quoted
notes, and word-wrapped prose instead of walls of text out of the box;
add `[format]\nenabled = false` if you'd rather keep the old raw-text
behavior. See [`docs/USAGE.md`](docs/USAGE.md) and
[`docs/COMMANDS.md`](docs/COMMANDS.md) for what's configurable, and
`relay.toml.example`'s inline comments for the full schema. `relay.toml`
is gitignored too (it holds no secret, but is local/user-specific).

### 4. Send your bot a message

Open a chat with your new bot in Telegram and send anything, e.g. `hi`.
This lets the bridge learn your numeric Telegram `user_id` (the allowlist
boundary).

### 5. Wire up an adapter (only if you're NOT using Claude Code)

**Claude Code users: skip this step.** The Claude Code adapter
(`adapters/claude-code.sh`, invoked via the `hook-notify.sh` shim) is
already wired into `~/.claude/settings.json`'s `hooks.SubagentStop` /
`hooks.Notification` — nothing to do. If you want more than these two
events (the adapter understands all 30 documented Claude Code hook
events), enable them in `relay.toml`'s `[claude_code.<Event>]` tables and
run `~/.claude/telegram-bridge/install-hooks.sh` to sync
`~/.claude/settings.json` to match — see the
[README's "Installing hooks" section](README.md#installing-hooks-for-more-events).

**Grok Build / Grok CLI:** run
`bash install-grok-hooks.sh` after optionally tuning `[grok.*]` in
`relay.toml`. See [`docs/ROUTING.md`](docs/ROUTING.md) if you also want
multi-backend chat isolation.

**Any other agent/harness:** either call
[`relay-notify.sh`](relay-notify.sh) directly wherever you want a status
ping (no adapter needed for plain text):

```bash
~/.claude/telegram-bridge/relay-notify.sh "your status text"
```

or write a small adapter if your harness has its own structured event
format worth parsing — copy
[`adapters/generic-example.sh`](adapters/generic-example.sh) and see
[`adapters/README.md`](adapters/README.md).

### 6. Go live

```bash
bash ~/.claude/telegram-bridge/go-live.sh
```

This validates the token (prints your bot's `@username`), auto-resolves
your `ALLOWED_USER_ID`/`ALLOWED_CHAT_ID` from the message you sent in
step 4 and writes them into `.env`, then DMs you
`🟢 Bridge live — Claude Code ↔ Telegram connected`.

You do not have to run this by hand — a Claude Code session can also
watch for the token and auto-detect it, go live, and start listening for
your inbound messages (`watch-go-live.sh`; `tg-poll.sh` re-checks `.env`
every ~15s until a token appears).

### 7. Verify

You should have received the "🟢 live" DM from step 6. Confirm both
directions:

- **Outbound:** trigger a subagent/notification in Claude Code (or run
  `relay-notify.sh "test ping"` directly) — you should get a DM.
- **Inbound:** reply to the bot with any text. If you've enabled the
  built-in commands (uncomment the `[commands.dashboard]` etc. blocks in
  `relay.toml` — see [`docs/COMMANDS.md`](docs/COMMANDS.md)), try
  `/help` — it should reply immediately, at zero model tokens, listing
  what's configured.

That's it. From then on:
- Subagent completions and Claude-needs-your-attention notifications DM
  your phone automatically (0 tokens).
- Anything you type to the bot either gets answered directly by a
  relay-handled command (0 tokens) or gets picked up by the session's
  Telegram monitor and treated as your next input (tokens spent only
  then).

## Voice messages (TTS) (optional)

`tg-send.sh` can send a **self-hosted, local voice note** instead of (or
alongside) the text message — no external TTS API is ever called, and the
feature is **off by default** (`mode = "off"` — no behavior change with no
`relay.toml`, or an existing one that doesn't set `[tts]`).

Pick ONE local engine (both are small; neither needs a GPU or a network
call at synthesis time):

**Option A — espeak-ng (zero-config fallback, robotic but always works):**

```bash
sudo apt install espeak-ng    # Debian/Ubuntu; tiny package
```

**Option B — piper (recommended: the quality path — natural-sounding,
still fully local/offline):**

```bash
# 1. Install piper — either works:
pip install --user piper-tts          # add --break-system-packages if pip
                                       # refuses on an externally-managed env
pipx install piper-tts                # cleaner: isolated venv, `piper` on
                                       # PATH via ~/.local/bin — no system
                                       # Python conflict (recommended)
# or download the prebuilt binary release:
#   https://github.com/rhasspy/piper/releases

# 2. Fetch a voice model — the one-liner:
./fetch-voices.sh              # deep male (en_US-joe-medium, default, ~60MB)
./fetch-voices.sh --list       # see all recommended voices (male + female)
./fetch-voices.sh amy          # fetch a specific one instead, e.g. amy
```

`fetch-voices.sh` downloads both files a voice needs (the `.onnx` model +
its `.onnx.json` config) into `./voices/`, verifies the download against a
pinned sha256, and skips re-downloading a voice that's already present and
checksum-clean. Recommended voices (see `./fetch-voices.sh --list` for the
live table):

| key        | model                    | character                                          |
|------------|--------------------------|-----------------------------------------------------|
| `joe`      | `en_US-joe-medium`       | **default** — deep, full male narrator               |
| `hfc_male` | `en_US-hfc_male-medium`  | alternative deep male, brighter timbre                |
| `ryan`     | `en_US-ryan-high`        | alternative male, higher/lighter, high-quality (~120MB) |
| `lessac`   | `en_US-lessac-medium`    | neutral/warm general-purpose narrator                 |
| `amy`      | `en_US-amy-medium`       | female, bright/conversational                         |

(If you'd rather fetch by hand: each voice's two files live at
`https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/<voice>/<quality>/<voice>-<quality>.onnx`
[`+.json`] — see the [piper voices index](https://github.com/rhasspy/piper/blob/master/VOICES.md)
for the full catalog beyond the ones above.)

Voice notes are transcoded to OGG/OPUS (what Telegram's `sendVoice`
expects) via **ffmpeg** (`sudo apt install ffmpeg`); with no ffmpeg
installed, the raw WAV is sent via `sendAudio` instead — still SOME voice,
never a hard failure. With **neither** engine installed, TTS is
skip-graceful: nothing voice-related happens, the text message still
sends exactly as always, and a one-line note lands in `.metrics.log`
(never a silent gap, never a failed send).

Then enable it in `relay.toml`:

```toml
[tts]
mode = "text+voice"   # or "voice-only"
engine = "piper"       # or "auto" (prefers piper if voice_model is set +
                        # the binary is present, else falls back to espeak-ng)
voice_model = "/root/.claude/telegram-bridge/voices/en_US-joe-medium.onnx"
length_scale = "0.81"  # OPTIONAL, piper only, default unset (piper's own
                        # 1.0 = unchanged cadence). Lower = faster speech,
                        # higher = slower — wired straight to piper's own
                        # `--length-scale` flag. 0.81 is this bridge's
                        # tuned, approved-final cadence for en_US-joe-medium
                        # — a brisk but natural pace for short chat notes.
# pitch = "-1"          # OPTIONAL, default unset (no shift) — a small
                        # negative semitone value nudges pitch a bit deeper
                        # still (duration-preserving; skip-graceful if the
                        # filter fails, falls back to unfiltered audio). NOT
                        # part of the recommended default above — the
                        # cadence tweak alone (length_scale) was judged
                        # sufficient; pitch stays off unless you want to
                        # experiment further. See lib/tts.sh's
                        # _tts_pitch_filter header.
max_chars = 600         # don't TTS a DIRECT/manual send longer than this
                        # — stays text-only. Automated hook pings follow
                        # the more permissive rule below instead.
hook_voice = true       # v0.5.1+, default true. A ping tagged
                        # TG_SEND_SOURCE=hook (Claude Code's
                        # SubagentStop/Notification hooks today) gets
                        # voice even when long/paginated, where max_chars
                        # above would otherwise skip it — this is what
                        # actually fixes "hook pings never get voice".
# --- spoken length (short is the default) ---
spoken_mode = "short"   # DEFAULT: one voice clip, truncated at a word
                        # boundary to spoken_max_chars. Text bubble stays
                        # full/unabridged either way.
spoken_max_chars = 600  # short-mode spoken-char cap (after strip)
# --- clean spoken transcript (v0.5.2+) ---
speak_code = false      # a code span/block is REFERENCED in the voice, not
                        # read char-by-char; true reads the code verbatim.
voice_code_ref = "ref. the message for the code"  # spoken in place of code
voice_link_ref = "ref. the message for the link"  # a [label](url) → "label,
                        # <ref>"; a bare URL → "link, <ref>". The URL chars
                        # are never voiced.
collapse_adjacent_refs = true  # DEFAULT true — consecutive identical
                        # code/link refs collapse to one spoken phrase.
```

#### Full-mode user recipe

Default voice is **short** (one clipped note). To have the whole message
read aloud — chunked into ordered clips instead of truncated — opt in:

```toml
[tts]
mode = "text+voice"
spoken_mode = "full"
clip_max_chars = 1500   # per-clip length (spoken chars after strip);
                        # 0 = one unbounded clip. hook_voice_max_chars is
                        # the legacy alias when clip_max_chars is unset.
collapse_adjacent_refs = true
voice_code_ref = "ref. the message for the code"
voice_link_ref = "ref. the message for the link"
```

Multi-clip sends log `tts hook_voice_chunked` to `.metrics.log`; short-mode
truncation logs `tts hook_voice_truncated`.

Before any voice note is synthesized, the message is stripped to plain-text
prose (`lib/tts_plain_text.py`) so the engine reads **words, not formatting
symbols** (`##`, `*`, `` ` ``, ```` ``` ````, `<b>`/`<pre>`, `&lt;`, `>`,
`[k/n]`, list markers). **Call/tool/request IDs** (UUIDs, `call_*`,
`toolu_*`, long hex tokens, …), **URLs**, and **code/backticks** are not
spoken — code and links become short spoken references; IDs are dropped.
**The sent text message keeps its full formatting and full length; only
the voice's input is stripped.** `spoken_max_chars` / `clip_max_chars`
count the *spoken* (post-strip) characters.

See `relay.toml.example`'s `[tts]` comments for the full schema, and
`lib/tts.sh`'s header for the engine-selection/transcode/pitch/cadence/send
pipeline.

### Guaranteed send ordering (v0.5.1+)

`tg-send.sh` serializes every send under an exclusive `flock`
(`.tg-send.lock`), so a burst of concurrent sends (several hook events
firing close together) go out one at a time instead of racing against the
Telegram API with no ordering guarantee:

```toml
[general]
send_interval_ms = 350   # default; delay held after each send finishes,
                          # before the next may begin — 0 disables the
                          # extra pause (mutual exclusion still applies)
```

Needs `flock` (util-linux — on essentially every Linux box already); if
it's missing, sends proceed unserialized exactly as before this feature
existed, logged once to `.metrics.log`, never a hard failure.

## Syntax-highlighted code (optional HTML-document tier)

Every fenced code block (` ```lang ... ``` `) already sends as an inline
`<pre><code class="language-X">` box, unconditionally, no setup needed —
`myc`/`mycelium` fences are aliased to `language-rust` by default so
Telegram's built-in Rust highlighter colors them today (see the README's
"Syntax-highlighted code" section for why: Telegram text has no color at
all, so a chat bubble alone can never show true per-token highlighting).

Opting into `[code_highlight] mode = "html-doc"` **additionally** sends a
self-contained, host-highlighted HTML document (real per-token color,
opened in the phone's browser) alongside the inline box. It needs
`pygments` (no Pillow — the HTML renderer is pure text generation):

```bash
pip install pygments    # add --break-system-packages if pip refuses on
                         # an externally-managed env
```

Nothing else to configure — with `pygments` not installed, `mode =
"html-doc"` just never sends a document (metric-logged, never
user-visibly broken); the inline box is unaffected either way. See
`relay.toml.example`'s `[code_highlight]` comments for the full schema
(mode, theme, line numbers, the `max_lines` cap, the `keep_text` caption,
and the `myc_inline_lang` alias), and `lib/code_highlight.sh`'s header
for the full never-silent contract.

## Security model

- **Allowlist by numeric `user_id`, not username.** `tg-poll.sh` only
  forwards messages from the sender whose id matches `ALLOWED_USER_ID` in
  `.env`. Every other sender is silently ignored — never forwarded,
  never logged with content. This is the actual security boundary; treat
  your bot token like a password (anyone with it can message as your bot,
  though they still cannot get past the allowlist to reach the agent).
- **Token lives only in `.env`** (mode `0600`, this user only).
  It is never printed, logged, or committed anywhere. **Never commit this
  file to any git repo.**
- **`relay.toml` holds no secret either** — it's gitignored for the same
  local/user-specific reasoning as `.env`, but contains no token or id.
- **Outbound-only.** Telegram bots work by your machine *polling out* to
  Telegram's servers (`getUpdates`) and *pushing out* status
  (`sendMessage`/`sendPhoto`) — there is no inbound network port opened on
  this machine. Nothing external can reach in.
- **Secret-scanned.** [gitleaks](https://github.com/gitleaks/gitleaks)
  runs as a pre-commit hook and a CI check on every push/PR, with a
  repo-specific rule (`.gitleaks.toml`) that catches this bot token's
  exact shape even without a `telegram`-looking variable name nearby.

## Caveat: the Claude Code hooks are global

The `SubagentStop`/`Notification` hooks are wired into
`~/.claude/settings.json`, which is your **global** user config — so they
fire for **every** Claude Code session on this machine, not just this repo,
until you narrow them (e.g. move them into a project's `.claude/settings.json`
with a path/matcher scoped to that project, or unset `BOT_TOKEN` when you
don't want pings). Because `tg-send.sh` no-ops silently with no token, an
otherwise-idle repo just does nothing — but once the token is set, expect a
DM from *any* session's subagents/notifications, not only this one.

## Files

See the repo [README](README.md#files) for the full, current file table
(generic core, adapters, `relay.toml`, `lib/`, `handlers/`). The files
that matter for day-one setup:

| File | Purpose |
|---|---|
| `.env` | Secrets/config (`BOT_TOKEN`, `ALLOWED_USER_ID`, `ALLOWED_CHAT_ID`), mode 0600 |
| `relay.toml` | Optional non-secret config (page size, hook events, commands) |
| `tg-send.sh` | Outbound: sends one Telegram message; silent no-op with no token |
| `tg-poll.sh` | Inbound: long-polls Telegram, prints `[telegram] <text>` per allowed message (run as a `Monitor` source) |
| `hook-notify.sh` | Hook shim: turns a `SubagentStop`/`Notification` hook payload into a short summary via `adapters/claude-code.sh` |
| `install-hooks.sh` | Sync `~/.claude/settings.json`'s Claude Code hooks to whatever's `enabled` in `relay.toml` — idempotent, merge-not-clobber; `--uninstall` reverses it |
| `go-live.sh` | One-shot activation: validate token, resolve your id, send the "live" DM |
| `.offset` | Persisted `getUpdates` offset (auto-created) |
| `.last-sent` | Last-sent message + timestamp, for outbound dedup (auto-created) |

**Not just Claude Code:** any other agent/script on this machine can push
a status update through the same bridge without touching hook JSON at
all — `~/.claude/telegram-bridge/relay-notify.sh "your status text"`. See
the README's [Harness-agnostic core](README.md#in-use) section and
[`docs/USAGE.md`](docs/USAGE.md), and `adapters/README.md` if you want to
integrate a harness that has its own structured event format worth
parsing.

## Next

Once you're live, see [`docs/USAGE.md`](docs/USAGE.md) for day-to-day
workflows and [`docs/COMMANDS.md`](docs/COMMANDS.md) for the built-in
commands and how to add your own.

# TG Agent Relay — setup

A secure, token-frugal bridge between your phone (Telegram) and this Claude
Code session (or, via `relay-notify.sh`/an adapter, any other agent or
harness — see the repo [README](README.md)). Everything lives under
`~/.claude/telegram-bridge/` (mode 0700, kept at this path deliberately —
see the README's repo/directory note) — no other repo is touched.

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
all) changes nothing. See [`docs/USAGE.md`](docs/USAGE.md) and
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
`hooks.Notification` — nothing to do.

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

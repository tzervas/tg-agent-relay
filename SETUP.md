# Telegram <-> Claude Code bridge — setup

A secure, token-frugal bridge between your phone (Telegram) and this Claude
Code session. Everything lives under `~/.claude/telegram-bridge/` (mode
0700) — no other repo is touched.

## How it stays token-frugal

- **Outbound status pings (phone <- Claude) cost ZERO model tokens.** They
  fire from Claude Code **hooks** (`SubagentStop`, `Notification`), which
  are plain shell script invocations by the harness — no model call
  involved. `tg-send.sh` is a pure `curl` POST to Telegram.
- **Inbound messages (phone -> Claude) only cost tokens when you actually
  send one.** `tg-poll.sh` long-polls Telegram for free (no model call); it
  only produces output — and therefore only wakes the session — when you
  send a real message. You are billed exactly for the turns you initiate.

## Setup steps

1. **Create your bot.** In Telegram, message **@BotFather** -> `/newbot` ->
   follow the prompts -> copy the token it gives you (looks like
   `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`).

2. **Add the token to .env.**

   ```bash
   # edit the file directly (recommended), or:
   echo 'BOT_TOKEN=<paste-your-token-here>' >> ~/.claude/telegram-bridge/.env
   ```

   If you use `echo >>`, remove the old empty `BOT_TOKEN=` line afterwards
   so there's only one. Editing the file directly is simplest — replace the
   empty `BOT_TOKEN=` line with `BOT_TOKEN=<your token>`. Keep it mode
   `0600` (it already is; nothing else needs to `chmod` it).

3. **Send your bot a message.** Open a chat with your new bot in Telegram
   and send anything, e.g. `hi`. This lets the bridge learn your numeric
   Telegram user_id (the allowlist boundary).

4. **Go live.**

   ```bash
   bash ~/.claude/telegram-bridge/go-live.sh
   ```

   This validates the token (prints your bot's `@username`), auto-resolves
   your `ALLOWED_USER_ID`/`ALLOWED_CHAT_ID` from the message you just sent
   and writes them into `.env`, then DMs you
   `🟢 Bridge live — Claude Code ↔ Telegram connected`.

   You do not have to run this by hand — the main Claude Code session also
   watches for the token and will auto-detect it, go live, and start
   listening for your inbound messages (`tg-poll.sh` re-checks `.env`
   every ~15s until a token appears).

That's it. From then on:
- Subagent completions and Claude-needs-your-attention notifications DM
  your phone automatically (0 tokens).
- Anything you type to the bot gets picked up by the session's Telegram
  monitor and treated as your next input (tokens spent only then).

## Security model

- **Allowlist by numeric `user_id`, not username.** `tg-poll.sh` only
  forwards messages from the sender whose id matches `ALLOWED_USER_ID` in
  `.env`. Every other sender is silently ignored — never forwarded,
  never logged with content. This is the actual security boundary; treat
  your bot token like a password (anyone with it can message as your bot,
  though they still cannot get past the allowlist to reach Claude).
- **Token lives only in `.env`** (mode `0600`, this user only).
  It is never printed, logged, or committed anywhere. **Never commit this
  file to any git repo.**
- **Outbound-only.** Telegram bots work by your machine *polling out* to
  Telegram's servers (`getUpdates`) and *pushing out* status
  (`sendMessage`) — there is no inbound network port opened on this
  machine. Nothing external can reach in.

## Caveat: the hooks are global

The `SubagentStop`/`Notification` hooks are wired into
`~/.claude/settings.json`, which is your **global** user config — so they
fire for **every** Claude Code session on this machine, not just this repo,
until you narrow them (e.g. move them into a project's `.claude/settings.json`
with a path/matcher scoped to that project, or unset `BOT_TOKEN` when you
don't want pings). Because `tg-send.sh` no-ops silently with no token, an
otherwise-idle repo just does nothing — but once the token is set, expect a
DM from *any* session's subagents/notifications, not only this one.

## Files

| File | Purpose |
|---|---|
| `.env` | Secrets/config (`BOT_TOKEN`, `ALLOWED_USER_ID`, `ALLOWED_CHAT_ID`), mode 0600 |
| `tg-send.sh` | Outbound: sends one Telegram message; silent no-op with no token |
| `tg-poll.sh` | Inbound: long-polls Telegram, prints `[telegram] <text>` per allowed message (run as a `Monitor` source) |
| `hook-notify.sh` | Hook shim: turns a `SubagentStop`/`Notification` hook payload into a short summary for `tg-send.sh` |
| `go-live.sh` | One-shot activation: validate token, resolve your id, send the "live" DM |
| `.offset` | Persisted `getUpdates` offset (auto-created) |
| `.last-sent` | Last-sent message + timestamp, for outbound dedup (auto-created) |

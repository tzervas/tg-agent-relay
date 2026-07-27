# Dedicated bots — one relay channel per agent

Run Claude and Grok on separate @BotFather bots, each with its own Telegram
chat and its own notification stream, instead of multiplexing both through one
bot with `@handle` prefixes.

Off by default. With no `[bots.*]` table the relay behaves exactly as it always
has — one `BOT_TOKEN`, one poll loop, the same `.offset` and `.tg-buffer*`
paths. Everything here is inert until you opt in.

## Why a bot id, not just a second token

**Telegram's `getUpdates` cursor is per bot.** Two poll loops sharing one
`<bridge>/.offset` each advance the cursor past updates the other never saw,
and messages disappear with no error anywhere. The reassembly buffers have the
same problem: add both bots to the *same* group and the chat id is identical,
so the per-chat buffer files collide.

So each bot needs isolated state, not just a different credential. Each gets a
state directory:

```
<bridge>/.offset                  default bot — legacy layout, unchanged
<bridge>/.bots/grok/.offset       bot id "grok"
<bridge>/.bots/grok/.tg-buffer.*  its own reassembly buffers
```

Isolation is a *directory* rather than a filename suffix, so every existing
glob keeps working verbatim against the new root.

## Configure

Only the **name** of the env var goes in `relay.toml`. Tokens stay in `.env`
(mode 0600) or the secret store.

```toml
[bots.claude]
token_env = "BOT_TOKEN"          # optional; defaults to BOT_TOKEN

[bots.grok]
token_env = "BOT_TOKEN_GROK"

[backends.fleet]
delivery = "fifo"
fifo = "~/.grok/telegram-bridge/sessions/fleet.fifo"
bot = "grok"                     # which bot serves this backend
```

Backends with no `bot =` belong to the default bot, so an existing config keeps
working untouched. To bind everything at once:

```toml
[routing]
default_bot = "grok"
```

## Set up a Grok bot

Steps 1–3 need your Telegram account — no agent can do them for you.

1. Message **@BotFather** → `/newbot` → copy the token.
2. Put it in `.env` (never in `relay.toml`):
   ```bash
   echo 'BOT_TOKEN_GROK=<token>' >> .env && chmod 600 .env
   ```
3. Add the bot to its chat or forum topic and send it one message. If
   `ALLOWED_USER_ID` is unset, the poller reports your id back to you.
4. Start the loops:
   ```bash
   bash scripts/ensure-inbound.sh --restart-poll
   ```

`ensure-inbound.sh` starts **one poll process per configured bot**, each with
its own pidfile and log:

```
.run/tg-poll-claude.pid   .run/logs/tg-poll-claude.log
.run/tg-poll-grok.pid     .run/logs/tg-poll-grok.log
```

With no `[bots.*]` table it starts a single loop under the original
`tg-poll.pid` / `tg-poll.log` names.

## Verify

```bash
ls .run/*.pid              # one pidfile per bot
cat .bots/grok/.offset     # per-bot cursor, isolated from the default bot
bash scripts/doctor-inbound.sh
```

Channels are enforced at delivery: a message arriving on the Grok bot is never
delivered into a backend owned by the Claude bot. A crossed attempt is recorded
as `message_filtered backend=… bot=… want=…` rather than delivered.

Outbound follows the same binding. `relay-notify.sh` and `tg-send.sh` pick the
token from `RELAY_BOT`, or derive it from `RELAY_BACKEND` (which the hook
scripts already export), falling back to `BOT_TOKEN`.

## Remote-controlled Grok session

Register a dedicated `@handle` session and attach its FIFO as the Grok
harness's Monitor:

```bash
bash scripts/register-session.sh --handle grok --type grok
adapters/backend-fifo-reader.sh ~/.grok/telegram-bridge/sessions/grok.fifo
```

Messages sent while that session is down are spooled and replayed in order when
it attaches — see [INBOUND-DELIVERY.md](INBOUND-DELIVERY.md).

To have a Telegram message *launch* work rather than feed an already-running
session, use `delivery = "cmd"`. The command receives `RELAY_TEXT`,
`RELAY_PROJECT`, `RELAY_CWD` and `RELAY_MODEL` in its environment;
`relay.toml.example` has worked examples.

Grok's return path already exists — `install-grok-hooks.sh` wires
`adapters/grok.sh` → `providers/grok/hooks.py`, covering all 14 Grok Build
events back through `relay-notify.sh`.

## Known gap

`lib/routing.sh` has no `.sessions.d` support, so the **shell fallback** poller
cannot route `@handle` session backends. Python is the production default
(#67), so this only matters if `RELAY_PYTHON_POLL=0` or the package fails to
import.

# claude-telegram-bridge

A small, secure, **token-frugal** bridge between a Telegram bot and a Claude Code
session, built with pure `curl` + `jq` (no framework).

## Design

Two channels, deliberately split so **status pings cost zero model tokens** and you
are only billed when *you* message the bot:

- **Outbound status → phone (0 model tokens):** Claude Code **hooks**
  (`SubagentStop`, `Notification`) fire `hook-notify.sh` → `tg-send.sh` → Telegram.
  Hooks run outside the model, so automated status never spends tokens.
- **Inbound phone → Claude (billed only here):** `tg-poll.sh` long-polls
  `getUpdates`, **allowlisted strictly to your numeric `ALLOWED_USER_ID`** (every
  other sender is silently ignored — the security boundary), and emits one line per
  allowed message for a Claude Code `Monitor` event source.

Telegram bots are **outbound-only** — no inbound port is exposed.

## Files

| File | Purpose |
|---|---|
| `.env.example` | Config template (committed). Copy to `.env` and fill `BOT_TOKEN`. |
| `.env` | **Local-only, gitignored, 0600** — holds the live bot token. Never committed. |
| `tg-send.sh` | Outbound `sendMessage`; silent no-op with no token; 10s dedup; auto-paginates (`[k/n]`) over `TG_PAGE_SIZE` (default 3500) chars. |
| `tg-poll.sh` | Inbound long-poll; strict id-allowlist; emits `[telegram] <text>`; reassembles a rapid burst into one event after a `TG_REASSEMBLE_WINDOW` (default 4s) quiet gap. |
| `hook-notify.sh` | Hook shim: parses hook JSON, forwards the FULL summary to `tg-send.sh` (which paginates it), capped only at an extreme `TG_HOOK_MAX_PAGES` outlier (default 6 pages) with a `[+M more pages omitted]` marker. |
| `go-live.sh` | Validates the token, auto-resolves your id, sends the "🟢 live" DM. |
| `watch-go-live.sh` | Optional: waits for a token to appear, then runs `go-live.sh`. |
| `SETUP.md` | Step-by-step setup + security notes. |

## Setup

See [`SETUP.md`](SETUP.md). In short: create a bot with `@BotFather`, copy the token
into a local `.env`, message the bot once, then `bash go-live.sh`.

## Security

- Allowlist by numeric `user_id`; all other senders ignored.
- Bot token lives only in the local `.env` (gitignored, 0600) — never in the repo.
- No inbound port (Telegram long-poll is outbound).
- Secret-scanned: [gitleaks](https://github.com/gitleaks/gitleaks) runs as a
  pre-commit hook (`.pre-commit-config.yaml`) and a CI check
  (`.github/workflows/gitleaks.yml`) on every push/PR.

## License

MIT — see [`LICENSE`](LICENSE). Copyright (c) 2026 Tyler Zervas.

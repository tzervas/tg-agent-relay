# TG Agent Relay

An **agent/harness-agnostic** Telegram relay: a small, secure,
**token-frugal** bridge between a Telegram bot and any coding agent or
automation harness, built with pure `curl` + `jq` + `python3` stdlib (no
framework, no external services).

> **Repo/directory note:** this repo is named `tg-agent-relay` on GitHub
> (renamed from `claude-telegram-bridge` — GitHub auto-redirects the old
> URL). The **local working copy stays at `~/.claude/telegram-bridge/`** —
> that path is baked into a live Claude Code hook config and other agents'
> script invocations, so it's left unchanged deliberately. The name
> mismatch (repo `tg-agent-relay`, directory `telegram-bridge`) is
> cosmetic only.

## Design

Two channels, deliberately split so **status pings cost zero model
tokens** and you are only billed when *you* message the bot:

- **Outbound status → phone (0 model tokens):** any agent/harness calls
  [`relay-notify.sh`](relay-notify.sh) (generic) or a harness-specific
  [adapter](adapters/) (e.g. Claude Code hooks → `hook-notify.sh` →
  [`adapters/claude-code.sh`](adapters/claude-code.sh)) → `tg-send.sh` →
  Telegram. Hook-driven pings run outside the model, so automated status
  never spends tokens.
- **Inbound phone → agent (billed only here):** `tg-poll.sh` long-polls
  `getUpdates`, **allowlisted strictly to your numeric `ALLOWED_USER_ID`**
  (every other sender is silently ignored — the security boundary), and
  emits one line per allowed message for a `Monitor`-style event source.
  A message can optionally be recognized as an **in-chat command**
  (`/status`, `status ...`) and tagged for the consuming agent — see
  [In-chat commands](#in-chat-commands-user--agent) below.

Telegram bots are **outbound-only** — no inbound port is exposed.

## Harness-agnostic core

The relay has one **generic entry point** any agent/harness can call
directly, plus optional **adapters** for harnesses whose native events are
worth parsing into structured, per-event messages.

```
                    ┌─────────────────────┐
 any agent/script → │  relay-notify.sh    │ → tg-send.sh → Telegram
 (free text / JSON) │ (generic core)      │
                    └─────────────────────┘
                              ▲
                    ┌─────────┴───────────┐
 Claude Code hooks →│ adapters/            │
 (hook-notify.sh    │  claude-code.sh      │
  shim, unchanged)  └──────────────────────┘
```

- **Generic status input (any agent/harness):**

  ```bash
  relay-notify.sh "Deploy finished OK"                 # raw text
  echo '{"label":"deploy","text":"finished OK"}' \
    | relay-notify.sh                                     # structured JSON
  ```

  See [`relay-notify.sh`](relay-notify.sh)'s header for the full usage
  (`--label`, `--raw`, stdin vs args). It's a drop-in replacement for
  calling `tg-send.sh` directly, plus optional structured
  label:text formatting and relay.toml-driven config.

- **Adapters** (see [`adapters/README.md`](adapters/README.md)) turn a
  harness's native event shape into a `relay-notify.sh` call. Today:
  Claude Code (`adapters/claude-code.sh`), plus a copy-paste stub
  (`adapters/generic-example.sh`) for writing your own.

- **`tg-send.sh` / `tg-poll.sh` stay harness-neutral** — a plain `curl`
  send and a plain stdout event stream, unchanged interfaces, exactly as
  before.

## Configurable via `relay.toml` (optional)

Copy [`relay.toml.example`](relay.toml.example) to `relay.toml` to
configure page size/delay, the reassemble window, which Claude Code hook
events are enabled + their prefix, the `[generic]` prefix, and in-chat
commands. **Every script falls back to its pre-existing env-var/hardcoded
default with no `relay.toml` present** — this is the backward-compat
guarantee: an existing bridge with no `relay.toml` behaves byte-for-byte
as it always has. See the example file's comments for the full schema.

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
behavior. See `relay.toml.example`'s `[commands.*]` section for the
schema (`keyword`, `slash`, `tag`).

A command can also be **relay-handled** (`mode = "relay"` + `handler`)
instead of forwarded — the relay runs a local script and answers
directly, at zero model tokens, instead of ever emitting the event to the
agent. This routing already works (`tg-poll.sh`'s `dispatch_command()`);
no built-in handler ships yet — see [`handlers/README.md`](handlers/README.md).

## Files

| File | Purpose |
|---|---|
| `.env.example` | Secret config template (committed). Copy to `.env` and fill `BOT_TOKEN`. |
| `.env` | **Local-only, gitignored, 0600** — holds the live bot token. Never committed. |
| `relay.toml.example` | Non-secret config template (committed). Copy to `relay.toml` to customize. |
| `relay.toml` | **Local-only, gitignored** — your actual config. Optional; scripts fall back gracefully without it. |
| `tg-send.sh` | Outbound `sendMessage`; silent no-op with no token; 10s dedup; auto-paginates (`[k/n]`) over `page_size`/`TG_PAGE_SIZE` (default 3500) chars. |
| `tg-poll.sh` | Inbound long-poll; strict id-allowlist; emits `[telegram] <text>` (or `[telegram:cmd:<tag>] <text>` for a recognized command); reassembles a rapid burst into one event after a quiet gap. |
| `relay-notify.sh` | **Generic, harness-agnostic entry point** — any agent/script can send a status update through it directly. |
| `adapters/claude-code.sh` | Claude Code hook-JSON adapter — parses the payload, formats a per-event summary, calls `relay-notify.sh --raw`. |
| `adapters/generic-example.sh` | Copy-paste stub for writing a new harness adapter. |
| `adapters/README.md` | How to write an adapter for any harness. |
| `hook-notify.sh` | **Backward-compatible shim** — wired into `~/.claude/settings.json`; just `exec`s `adapters/claude-code.sh`. |
| `lib/relay-config.sh` | Optional `relay.toml` loader (`cfg_get`/`cfg_has_section`), shared by every script. |
| `lib/relay-common.sh` | Shared helpers (`oneline`, `cap_if_huge`, `emit_metric`). |
| `lib/toml_to_json.py` | `relay.toml` → JSON (Python stdlib `tomllib`; the only TOML-parsing code in the repo). |
| `handlers/` | Relay-handled-command scripts for `mode = "relay"` in `relay.toml` (a routing seam — see [`handlers/README.md`](handlers/README.md); no built-in handler ships yet). |
| `.metrics.log` | **Local-only, gitignored, auto-created** — a TSV event log (`emit_metric`) for a future dashboard; not read by anything yet. |
| `go-live.sh` | Validates the token, auto-resolves your id, sends the "🟢 live" DM. |
| `watch-go-live.sh` | Optional: waits for a token to appear, then runs `go-live.sh`. |
| `SETUP.md` | Step-by-step setup + security notes. |
| `ROADMAP.md` | Where this is headed. |

## Setup

See [`SETUP.md`](SETUP.md). In short: create a bot with `@BotFather`, copy
the token into a local `.env`, message the bot once, then
`bash go-live.sh`.

## Security

- Allowlist by numeric `user_id`; all other senders ignored.
- Bot token lives only in the local `.env` (gitignored, 0600) — never in
  the repo. `relay.toml` (if you create one) holds no secret either.
- No inbound port (Telegram long-poll is outbound).
- Secret-scanned: [gitleaks](https://github.com/gitleaks/gitleaks) runs as
  a pre-commit hook (`.pre-commit-config.yaml`) and a CI check
  (`.github/workflows/gitleaks.yml`) on every push/PR.

## License

MIT — see [`LICENSE`](LICENSE). Copyright (c) 2026 Tyler Zervas.

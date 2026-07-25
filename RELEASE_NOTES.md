## v0.10.3 (2026-07-25)

Security + correctness patch. **Upgrade promptly if you run hooks.**

### Highlights

- **Security — code injection via hook payloads.** `relay-notify.sh` and
  `adapters/claude-code.sh` interpolated `tool_name` and the hook event name
  into the *source text* of a `python -c` program. `tool_name` is whatever tool
  the model asked for, including a name supplied by an MCP server, so a crafted
  name closed the string literal and ran arbitrary code inside the relay's own
  interpreter — while the notification still delivered normally, so nothing
  looked wrong from Telegram. Both sites now pass values through the
  environment. If you run the relay with hooks wired to any agent that can load
  third-party MCP servers, treat this as the reason to upgrade.
- **Fixed: the relay could go silently deaf.** If `tg_agent_relay` was not
  importable — a deployed bridge without the package on its path, a partial
  deploy — the goal-noise filter's failure was read as "policy said drop this"
  and *every* hook notification was discarded with nothing logged. The filter
  now fails open. Set `RELAY_DEBUG=1` to see when it does.
- **Fixed: `[sessions] dir` in `relay.toml` was ignored.** Routing only consulted
  the session registry when an internal `_bridge_dir` was set, so configuring
  sessions the documented way produced no session backends at all — `@handle`
  prefixes silently did not resolve. Now honoured, along with
  `$RELAY_SESSIONS_DIR`.
- **CI is fail-closed.** The fleet python gate could not fail a job (`|| true`,
  and an `echo` standing in for a failure). A green `python lint/test` badge on
  `main` was reporting nothing. Missing tooling is now `FAIL_ENV`; the advisory
  trivy job is labelled advisory.

### Deploy

```bash
git fetch --tags && git checkout v0.10.3
bash scripts/deploy-local.sh --ref v0.10.3
```

Deployed bridges that were rsynced from a working tree rather than checked out
will not pick this up from `git pull` — verify with `cat .deploy-stamp` and
redeploy from the tag.

---

## v0.10.2 (2026-07-21)

### Highlights

- **Docs polish** — `RELEASE_NOTES.md` had skipped straight from v0.9.0 to
  the CHANGELOG's v0.10.0/v0.10.1; backfilled below so operators upgrading
  off v0.9.0 have full deploy notes for each step.
- No functional/code changes vs v0.10.1.

### Deploy

```bash
git fetch --tags && git checkout v0.10.2
bash scripts/deploy-local.sh --ref v0.10.2
```

---

## v0.10.1 (2026-07-20)

### Highlights

- **Inbound FIFO fix** — `ensure-inbound` no longer starts log-draining FIFO
  readers that steal messages from Grok/Claude Monitors; it only runs
  `tg-poll` and RDWR keepalives (no read). Dual readers on the same FIFO
  path are deduped; agent Monitors own the read path.
- `/status` ships as a zero-token relay-mode alias of `/stats`.
- README architecture diagram: relay vs agent split, keepalives,
  multi-backend FIFOs (sanitized).

### Deploy

```bash
git fetch --tags && git checkout v0.10.1
bash scripts/deploy-local.sh --ref v0.10.1
```

---

## v0.10.0 (2026-07-16)

### Highlights

- **Forum threads (P13)** — topic title builders, outbound resolve order,
  mockable `createForumTopic`, overlay bind helpers (`tg_agent_relay/threads.py`).
- **`/thread` commands** — `handlers/thread.sh` + `[commands.thread]`
  example: `list`, `here`, `bind`, `ensure`.
- **Outbound thread routing** — `relay-notify.sh` sets `RELAY_CHAT_ID` /
  `RELAY_THREAD_ID` from session/platform/workstream/handle, with optional
  `🧵` title stamp.
- **Docs** — `docs/THREADS.md`, cross-linked from `docs/ROUTING.md`.

### Deploy

```bash
git fetch --tags && git checkout v0.10.0
bash scripts/deploy-local.sh --ref v0.10.0
```

---

## v0.9.0 (2026-07-16)

### Highlights

- **Goal hook noise** — inactive-goal `update_goal` failures no longer spam Telegram.
- **Plan approve** — PLAN messages ship inline Approve / Reject; replies route as `[telegram:plan]`.
- **Voice** — PLAN / long / multi-page messages auto-use full spoken mode + multi-clip TTS.
- **Usage UX** — usage chart PNGs include 24h / 7d / 30d / Refresh buttons.

### Deploy

```bash
git fetch --tags && git checkout v0.9.0
bash scripts/deploy-local.sh --ref v0.9.0
```

---

### Highlights (v0.6.1)

- **Python send/poll is the default** — `tg-send.sh` / `tg-poll.sh` exec the package when import works; shell remains recovery and opt-out (`RELAY_PYTHON_SEND=0` / `RELAY_PYTHON_POLL=0`). See `docs/DECISIONS.md` (D1).
- **Shell recovery** — clear first-failure notes, sticky re-probe window, secret redaction, validated `RELAY_PYTHON`, bounded import probe (`lib/python_fallback.sh`).
- **Providers** — OpenAI / ChatGPT + OpenAI-compatible self-host plug-and-play; optional Google ADK soft-import and MCP extension bus (tools without requiring a model).
- **Grok hooks ≥ Claude path** (epic #60) — install dry-run / no-op / fail-closed; richer `format_hook` summaries; optional tool matchers; quiet vs full profiles; 14-event fixtures + adapter e2e; live smoke + metrics checklist (`docs/GROK_HOOKS.md`).
- **TTS** — emoji/pictographs stripped from the **voiceover** transcript only (on-screen text unchanged).
- **Deploy** — local deploy syncs `tg_agent_relay/` for the Python default path.

### Upgrade notes

```bash
git fetch --tags && git checkout v0.6.1
bash scripts/deploy-local.sh --ref v0.6.1
# Grok operators:
bash ~/.claude/telegram-bridge/install-grok-hooks.sh   # or repo path
# Restart tg-poll / Monitor if needed
```

If you need the previous shell-only send/poll path while debugging:

```bash
export RELAY_PYTHON_SEND=0 RELAY_PYTHON_POLL=0
```

### Testing (local gate)

Targeted offline coverage for the surfaces above (install suite, provider/hook units, Grok adapter e2e, format fixtures, shell e2e with Python forced off for curl stubs) — not blanket 100% coverage. Full gate: `bash scripts/local-ci.sh --release`.

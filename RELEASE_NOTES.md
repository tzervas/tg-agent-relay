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

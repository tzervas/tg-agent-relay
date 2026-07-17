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

# Grok Build hooks — quality bar vs Claude

**Epic:** [#60](https://github.com/tzervas/tg-agent-relay/issues/60)  
**Process:** [WORKFLOW.md](WORKFLOW.md) (swarm with **Grok Build**, not flagship 4.5)  
**Official events:** `~/.grok/docs/user-guide/10-hooks.md` (14 events; we catalog all)  
**Operator install / quiet vs full / troubleshooting:** [PROVIDERS.md](PROVIDERS.md#grok-build-telegram-hooks) · [SETUP.md](../SETUP.md) (Grok under “Wire up an adapter”)

## Baseline (already good)

| Area | Grok today | Claude reference |
|---|---|---|
| Event coverage | **14/14** documented | 30 Claude events |
| Implementation | Python `providers/grok/hooks.py` | `providers/claude/hooks.py` |
| Adapter | Thin `adapters/grok.sh` → provider_hook | Thin/hybrid → provider_hook (default) |
| Install | Catalog-driven `install-grok-hooks.sh` | Catalog-driven `install-hooks.sh` |
| Fixtures | 14 JSON under `tests/fixtures/hooks/grok/` | Partial sample set |
| Defaults on | Stop, StopFailure, SubagentStop, Notification, PostToolUseFailure | Similar quiet lifecycle set |
| Dispatch | Smart `hook-notify` + `hook-notify-grok` | `hook-notify` → claude adapter |
| Routing | `project_from_cwd` + RELAY_BACKEND=grok | Same pattern |

## Delivered (epic #60 — closed via #61–#66)

Epic [#60](https://github.com/tzervas/tg-agent-relay/issues/60) and swarm children are **done** on the product line (landed with [PR #68](https://github.com/tzervas/tg-agent-relay/pull/68) / the v0.6.1-dev stack). Offline quality bar is met in-repo; residual work is **operator live soak**, not more swarm code.

| Area | Delivered | Issue |
|---|---|---|
| Install UX | Dry-run plan, explicit no-op, fail-closed malformed, merge narrative | #61 |
| Summary fidelity | Richer templates / field extraction / tests | #62 |
| Test depth | Config override + e2e paths offline | #63 |
| Matchers | Grok native matcher wired where useful | #64 |
| Operator docs | PROVIDERS / SETUP / this doc | #65 |
| Live smoke doc | Checklist + metrics (below) | #66 |

## Quality bar (met offline; confirm live)

1. **Install** never leaves broken JSON; second run is silent no-op when unchanged.  
2. **Phone UX** one-line summaries as clear as Claude (emoji prefix + useful detail, no spam).  
3. **Config** `[grok.<Event>]` enabled / prefix / format (+ optional matcher).  
4. **Tests** offline, no live Telegram; all 14 fixtures exercised through format + key adapter paths.  
5. **Docs** new user can wire Grok→Telegram without reading source.

## Non-goals (still out of scope)

- PreToolUse **blocking** policy engine (notify-only default)  
- Editing Claude settings from Grok installer  
- Forcing all 14 events default-on (noise)

## Residual live checklist (human)

Use the operator smoke section below after deploy. Do **not** reopen #60–#66 for soak-only work unless offline AC regresses.

## Operator live smoke + metrics

After deploy + `install-grok-hooks.sh`, use this short checklist to confirm
Grok → Telegram quality. Install / profiles / troubleshooting remain in
[PROVIDERS.md](PROVIDERS.md#grok-build-telegram-hooks). Release/deploy steps:
[RELEASING.md](RELEASING.md).

Do **not** paste bot tokens, chat IDs, or `.env` contents into tickets or
docs — only confirm those files exist and that Telegram receives a ping.

### Preconditions

1. Bridge deployed (`bash scripts/deploy-local.sh` or your usual path).
2. `bash install-grok-hooks.sh --dry-run` then install if the plan is right.
3. `.env` has `BOT_TOKEN` and allowlist (`ALLOWED_USER_ID` / `ALLOWED_CHAT_ID`)
   on the **deployed** bridge (never commit or paste secrets).
4. Restart the Grok session so hooks reload; confirm the Hooks tab shows the
   `tg-agent-relay` commands.
5. Optional: note the tail of `.metrics.log` so new lines are obvious:
   ```bash
   # Deployed bridge path is typically ~/.claude/telegram-bridge/
   BRIDGE="${BRIDGE:-$HOME/.claude/telegram-bridge}"
   tail -n 5 "$BRIDGE/.metrics.log" 2>/dev/null || true
   ```

### Event checklist (quiet defaults)

Quiet profile defaults **on**: `Stop`, `StopFailure`, `SubagentStop`,
`Notification`, `PostToolUseFailure`. Everything else is off unless you
enabled a full profile.

| Check | How to trigger (live Grok) | Telegram | `.metrics.log` (source / event) |
|---|---|---|---|
| **Stop** | Finish a short agent turn | One line with Stop prefix (`🏁` by default) | `hook` / `grok_event` — detail contains `event=Stop` |
| **SubagentStop** | Run work that spawns a subagent and let it finish | Subagent-finished line (default prefix from catalog) | `hook` / `grok_event` — `event=SubagentStop` |
| **PostToolUseFailure** | Cause a tool to fail (bad path, denied cmd, etc.) | Failure line (`⚠️` by default) | `hook` / `grok_event` — `event=PostToolUseFailure` |
| **Notification** | Any agent notification that fires the Notification hook | Notification line | `hook` / `grok_event` — `event=Notification` |
| **Disabled silent** | Rely on a default-off event (e.g. `PreToolUse` / `SessionStart`) without enabling it | **No** Telegram ping for that event | Optional `hook` / `grok_skip` with detail `disabled:<Event>` — skip is metrics-only, not a phone message |

Offline sanity (no Grok UI; still exercises the adapter + metrics names):

```bash
BRIDGE="${BRIDGE:-$HOME/.claude/telegram-bridge}"   # or repo root for a dry tree
cd "$BRIDGE"   # adapters resolve relative to their install tree

# Enabled quiet events → grok_event (+ Telegram if .env is configured)
cat tests/fixtures/hooks/grok/Stop.json | bash adapters/grok.sh
cat tests/fixtures/hooks/grok/SubagentStop.json | bash adapters/grok.sh
cat tests/fixtures/hooks/grok/PostToolUseFailure.json | bash adapters/grok.sh
cat tests/fixtures/hooks/grok/Notification.json | bash adapters/grok.sh

# Default-off → grok_skip disabled:… and no useful phone ping
cat tests/fixtures/hooks/grok/PreToolUse.json | bash adapters/grok.sh
cat tests/fixtures/hooks/grok/SessionStart.json | bash adapters/grok.sh

grep $'\thook\tgrok_' .metrics.log | tail -n 20
```

Expect detail strings like:

- success: `backend=grok event=Stop cwd=…`
- disabled: `disabled:PreToolUse` / `disabled:SessionStart`
- missing Python path: `python3_or_provider_hook_missing`
- empty/bad payload path: `empty_or_error` or `SKIP:…` reasons from `provider_hook`

### Hook voice short mode (`TG_SEND_SOURCE=hook`)

Grok uses the **same** hook-voice path as Claude:

1. `adapters/grok.sh` sends via `TG_SEND_SOURCE=hook "$BRIDGE/relay-notify.sh" --raw …`
2. `tg-send.sh` treats `TG_SEND_SOURCE=hook` as an automated ping.
3. With defaults (`[tts] hook_voice = true`, `spoken_mode = "short"`), a hook
   ping gets **text (full)** plus **one short voice note** (spoken prose
   capped at `spoken_max_chars`, default 600), even when a direct send
   would skip voice for length.

**How to verify (Grok, same expectations as Claude):**

1. Ensure TTS is actually enabled for outbound sends, e.g. in `relay.toml`:
   ```toml
   [tts]
   mode = "text+voice"   # or "voice-only"; hook text still always sends
   hook_voice = true
   spoken_mode = "short"
   spoken_max_chars = 600
   ```
2. Trigger a **Stop** (or any quiet event) with a moderately long assistant
   summary so spoken length can exceed `spoken_max_chars` if you want the
   truncate metric.
3. On the phone: full text bubble **and** a voice note (not text-only).
4. In metrics, look for TTS lines from the same send window:
   ```bash
   grep $'\ttts\t' "$BRIDGE/.metrics.log" | tail -n 20
   ```
   - Short-mode truncation: event `hook_voice_truncated` (detail includes
     `mode=short` / spoken char counts).
   - Engine send: event `sent` (or `skip` with an engine reason if Piper /
     espeak is missing — install per SETUP.md).
5. Optional parity check: force the same summary through Claude’s adapter
   vs Grok’s; both must pass `TG_SEND_SOURCE=hook` into `tg-send`. For a
   unit-level Claude proof see `tests/run-tests.sh` (“TG_SEND_SOURCE=hook
   propagates…”). Grok’s export is the one-liner in `adapters/grok.sh`
   (`TG_SEND_SOURCE=hook` on the `relay-notify` call).

If voice never appears: confirm `hook_voice` is not `false`, a local TTS
engine is installed, and the metric source is `hook` / `grok_event` (so the
adapter did attempt notify). Missing token still yields a silent send no-op.

### Metrics hygiene — exact strings

Log path: **`$BRIDGE/.metrics.log`** (local-only, gitignored). Format is TSV:

```text
<epoch>\t<source>\t<event>\t<detail>
```

| Source | Event | When | Detail (examples) |
|---|---|---|---|
| `hook` | `grok_event` | Provider returned `OK:…`; notify attempted | `backend=grok event=Stop cwd=/path` |
| `hook` | `grok_skip` | Disabled, empty, or hard skip | `disabled:PreToolUse`, `empty_or_error`, `python3_or_provider_hook_missing` |
| `relay-notify` | `generic_send` | Notify path ran | often `raw backend=grok` |
| `tts` | `hook_voice_truncated` / `hook_voice_chunked` / `sent` / `skip` | Voice path | spoken / engine detail |

**Distinguishability:** Claude’s shell adapter logs per-Claude-event names
(e.g. source `hook`, event `Stop` or `Stop_skip`). Grok always uses the
fixed events **`grok_event`** and **`grok_skip`**, with the Grok hook name
inside the **detail** (`event=Stop`, `disabled:SessionStart`, …). Grep:

```bash
# Grok only
grep $'\thook\tgrok_event\t' "$BRIDGE/.metrics.log"
grep $'\thook\tgrok_skip\t'  "$BRIDGE/.metrics.log"

# Easy filter for either outcome
grep $'\thook\tgrok_' "$BRIDGE/.metrics.log" | tail -n 40
```

No adapter rename is required for hygiene: `grok_event` / `grok_skip` do not
collide with Claude’s event field names.

### Smoke sign-off (copy/paste)

```text
[ ] Stop → Telegram + hook/grok_event (event=Stop)
[ ] SubagentStop → Telegram + grok_event (event=SubagentStop)
[ ] PostToolUseFailure → Telegram + grok_event (event=PostToolUseFailure)
[ ] Notification → Telegram + grok_event (event=Notification)
[ ] Disabled event (e.g. PreToolUse) → no Telegram; optional grok_skip disabled:…
[ ] TG_SEND_SOURCE=hook short voice: text + voice note; tts metrics as expected
[ ] No secrets pasted into chat/issue/PR
```

## Resume

```bash
gh issue list --label epic:providers --state open
cat docs/GROK_HOOKS.md docs/WORKFLOW.md
```

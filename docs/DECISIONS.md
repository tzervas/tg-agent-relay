# Design decisions

Internal notes for choices that shaped the implementation. User-facing
docs (README, SETUP) stay short and practical; this file records **why**.

## How to write a decision

When a non-trivial design is implemented, add a short entry here (or link
from the epic issue). Keep the tone plain and professional.

| Field | Content |
|---|---|
| **Context** | What problem or constraint forced a choice |
| **Decision** | What we shipped |
| **Why** | The reasons that mattered (cost, safety, UX, operability) |
| **Alternatives considered** | What else was on the table |
| **Why not** | Why each alternative lost (specific, not slogans) |
| **Where** | Code / config surfaces that embody the decision |

Do not restate general engineering values as a checklist. Let the
decision and the code speak; comments and docstrings explain the *local*
why of non-trivial logic (Google style in Python).

---

## D1 — Python is the default send/poll path

**Context.** Shell and Python implementations both existed after the
package port (#18 / #67). Operators needed a single default that matches
where new features land, without stranding existing bridge installs.

**Decision.** `tg-send.sh` / `tg-poll.sh` attempt the Python package first
and exec into it when import succeeds. Shell remains the body of those
scripts for recovery and for explicit opt-out
(`RELAY_PYTHON_SEND=0` / `RELAY_PYTHON_POLL=0`).

**Why.**

- New work is Python-first; dual-maintaining feature parity forever is
  expensive if shell stays the live default.
- Thin shell wrappers keep install paths and hook entrypoints stable
  (`~/.claude/telegram-bridge/tg-send.sh` does not need renaming).
- Operators can still force shell for bisect without a second binary.

**Alternatives considered**

| Alternative | Why rejected |
|---|---|
| Python-only entrypoints (`tg-relay-send` as sole path) | Breaks existing hook installs and muscle memory; harder dual-run during migration. |
| Shell default until a flag flip day | New features would keep landing in two places; migration debt grows. |
| Auto-detect once at install, hard-code path | Stale after deploy of `tg_agent_relay/`; import-time check matches reality. |

**Where.** `tg-send.sh`, `tg-poll.sh`, `lib/python_fallback.sh`,
`lib/python.sh`; user notes in SETUP / RELEASING.

---

## D2 — Sticky, redacted shell recovery after Python failure

**Context.** Hooks can fire many times per minute. If the package is
missing or the interpreter is broken, re-running `import` on every event
adds latency and log noise. Error text may also contain tokens.

**Decision.** On failure, record a short-lived stamp under the bridge dir
and use shell for the TTL (default 60s, max 1h). Redact common secret
shapes before stderr/metrics. Bound the import probe with `timeout` when
available. Validate `RELAY_PYTHON` before `exec`.

**Why.**

- Operators still see a clear first failure and how to fix it.
- Repeated sticky hits stay quiet by default so hook storms do not flood
  stderr with the same message.
- Metrics keep a trail without repeating secrets.

**Alternatives considered**

| Alternative | Why rejected |
|---|---|
| Silent shell fallback | Hides deploy mistakes (missing package) for a long time. |
| Fail hard (exit non-zero, no shell) | Drops outbound status / inbound poll — worse than degraded shell path for a bridge. |
| Re-probe every invocation | Correct but expensive under outage; multiplies log noise on busy hooks. |
| Permanent “use shell” flag after first failure | Requires manual reset; sticky TTL self-heals after a good deploy. |
| Log full exception text | Risk of tokens in `.metrics.log` and shared terminals. |

**Where.** `lib/python_fallback.sh`; env knobs documented in SETUP /
RELEASING.

---

## D3 — Strip emoji from TTS voiceover only

**Context.** Hook and status text often include emoji. Piper/espeak tend
to mis-speak or oddly pause on those glyphs. On-screen Telegram text
should keep them.

**Decision.** `lib/tts_plain_text.strip_formatting` removes emoji and
related pictograph sequences from the **spoken** transcript only.

**Why.** Voice quality and clarity matter more than reading decoration
aloud. Keeping emoji on the text message preserves scan-ability in chat.

**Alternatives considered**

| Alternative | Why rejected |
|---|---|
| Speak emoji as words (“check mark”, “rocket”) | Noisy and language-specific; needs a large map and still sounds odd. |
| Strip emoji from the sent Telegram message too | Loses useful visual structure users already rely on. |
| Depend on a third-party `emoji` package | Extra runtime dep for a stdlib-capable transform. |
| Engine-specific filters only | Behavior would diverge across piper vs espeak; one strip keeps parity. |

**Where.** `lib/tts_plain_text.py` (`strip_emoji`), used via `lib/tts.sh`
and `tg_agent_relay.tts`.

---

## Related

- Orchestration process (cost lanes, swarm roles): [WORKFLOW.md](WORKFLOW.md)
- Tooling / MSRV: [TOOLING.md](TOOLING.md)
- Release cutover notes: [RELEASING.md](RELEASING.md)

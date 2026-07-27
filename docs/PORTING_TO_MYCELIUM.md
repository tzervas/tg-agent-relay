# Porting `tg-agent-relay` to Mycelium — readiness & gap analysis

**Status:** staging / planning. Part of the `claude/mycelium-readiness-gaps` review
(2026-07-22), measured against the Mycelium Rust train **`v0.464.0`**.

`tg-agent-relay` is one of two designated first-port targets for Mycelium (the other
is `gha-runner-ctl`). This document records what "porting to Mycelium" concretely
means for this repo and what the language must grow first.

## What "porting this" actually means

This repo is a **hybrid mid-migration**, not a Rust program:

- **Python is the current core** (~20k LOC incl. tests; `send.py` + `poll.py` +
  `tg_agent_relay/` + `lib/`). It is now the default execution path.
- **Shell is the legacy/fallback tier** (~12k LOC; `tg-poll.sh`, `tg-send.sh`, …),
  invoked when Python is absent.
- **Rust does not exist yet** — `Cargo.toml` is `[workspace] members = []` (aspirational,
  "optional hotspots after the Python core"). **There is no Rust to translate.**

So a native port means **reimplementing the current Python twin** (`send.py`/`poll.py`
+ `tg_agent_relay/` + `lib/`) in Mycelium — not transpiling Rust. (The Rust→Mycelium
`--vet` profiler is therefore N/A here; there is no `.rs` to measure.)

Encouraging property for a young language: the load-bearing core has **zero third-party
dependencies** — it rides Python's stdlib (`urllib`+`ssl`, `json`, `tomllib`,
`subprocess`) and a few external binaries (`curl`, `jq`, `piper`). No framework to
reproduce.

## Architecture (what must be reproduced)

- **Outbound** (host→phone): lifecycle hooks → `relay-notify.sh`/adapter → `tg-send`
  which paginates over Telegram's 4096-char cap (`[k/n]`), dedups, optionally renders
  TTS/■code, and POSTs to the Telegram Bot API.
- **Inbound** (phone→host): `poll.py` **long-polls** `getUpdates`, strictly allowlisted
  to one `ALLOWED_USER_ID` (the security boundary), reassembles bursts, answers built-in
  `/status /stats /dashboard` locally at **zero model tokens**, else routes onto a
  backend **FIFO** the agent harness reads.
- Fully **synchronous, single-threaded** blocking long-poll — **no `asyncio`, no
  threads, no sockets opened directly**. Concurrency is by *process separation*.
  (So, as with `gha-runner-ctl`, Mycelium's missing `async` is **not** a blocker.)
- The **core relay** talks to `api.telegram.org` (+ its file-download host; `huggingface.co`
  only in `fetch-voices.sh`) and does **not** call an LLM itself — "providers" are primarily
  local hook/usage descriptors. (Some *optional* provider integrations reach other hosts —
  e.g. `generativelanguage.googleapis.com` (ADK/Gemini), `openrouter.ai` — so "one host" is
  the core-relay path, not an absolute.)

## Capability map: native-now vs. new-stdlib vs. Rust-bridge

| Capability | Class | Detail |
|---|---|---|
| Pagination/`[k/n]` chunking, dedup, message reassembly, template/format rendering, TTS **text-prep**, usage accounting, routing (longest-prefix) | **(a) native today** | Pure string/collection/logic — the **majority** of the ~13k non-test Python lines. This is where Mycelium can genuinely shine. |
| Offset/buffer/metrics-TSV/config-overlay file I/O | (a) | Covered by the thin `std-sys` fs floor (richer `std-fs` is in-memory only, M-541) |
| **TOML** parsing (`relay.toml`, 46 KB surface) | **(b) new stdlib** | no TOML parser; writable in-language but non-trivial |
| **JSON** parse/serialize (Telegram responses, hook payloads) | **(b) new stdlib** | pure; today only `Value`↔JSON exists |
| **HTTPS + TLS** client to `api.telegram.org` | **(c) Rust-bridge** | no sockets/TLS **and** no FFI host to bridge through |
| **Blocking long-poll** read with timeout (`getUpdates?timeout=50`) | **(c) Rust-bridge** | depends on the HTTP/socket layer |
| **Subprocess** spawning (adapters, handlers, `piper`/`ffmpeg`) | **(c) Rust-bridge** | no process API + no FFI host |
| **Named-pipe (FIFO)** IPC — the inbound→agent transport | **(c) Rust-bridge** | Unix `mkfifo`/open semantics; no host API |
| TTS synthesis, syntax highlight, dashboard PNG | (c) / defer | external binaries — shell out (needs subprocess) or keep out-of-process |

## Ranked blockers for this port

1. **FFI / host-effect execution path** (upstream language gap; `wild {}` does not
   execute yet). Gates every (c) row — no Rust shim is callable without it. **Linchpin.**
2. **HTTPS + TLS client** (single host — a narrow, well-scoped shim).
3. **Blocking socket read with timeout** (the long-poll).
4. **Subprocess/exec** (adapters/handlers/voice).
5. **JSON** and **TOML** codecs (buildable natively).
6. **FIFO IPC** (Unix-specific host capability).

## Bottom line

Until Mycelium can bridge at minimum **HTTPS/TLS + JSON** (which itself requires the
FFI host to land), a native port can faithfully reproduce the relay's *formatting /
routing / accounting brain* — and should, as dogfood — but **cannot talk to Telegram
or the agent harness on its own**. The pure brain is the majority of the code and is
portable now; the I/O shell is a small but absolutely load-bearing Rust-bridge surface
gated on the upstream linchpin.

See the umbrella planning doc in `mycelium-lang`
(`docs/planning/PORT-READINESS-2026-07-22.md`) and the per-component gap notes in
`mycelium-std-sys`, `mycelium-std-io`, and `mycelium-l1`.

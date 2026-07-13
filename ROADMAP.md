# TG Agent Relay — Roadmap

Goal: a **platform/agent/harness-agnostic** Telegram relay — integrate
with ANY agent using ANY harness, maximum portability + usability.

## Done

- `tg-send.sh` — outbound curl→Telegram, `[k/n]` pagination, dedup,
  no-op-without-token. Harness-neutral.
- `tg-poll.sh` — inbound long-poll, strict user-id allowlist, burst
  reassembly, emits plain `[telegram] <text>` on stdout, or
  `[telegram:cmd:<tag>] <text>` for a recognized in-chat command.
  Harness-neutral (stdout is universal); sourceable for tests.
- **Publish + rename.** Public MIT repo, gitleaks-scanned; renamed
  `claude-telegram-bridge` → `tg-agent-relay` (GitHub auto-redirects the
  old URL). Local working directory stays `~/.claude/telegram-bridge/`
  deliberately — see README's repo/directory note.
- **Agent/harness-agnostic core.** `relay-notify.sh` — a generic entry
  point ANY agent/harness can call directly with free text or
  `{"label":..,"text":..}` JSON. The Claude-Code-specific hook-JSON
  parsing moved out of `hook-notify.sh` into `adapters/claude-code.sh`
  (one adapter among possibly several); `hook-notify.sh` is now a thin,
  backward-compatible shim that `exec`s it, so the live
  `~/.claude/settings.json` hook wiring is unchanged. `adapters/README.md`
  + `adapters/generic-example.sh` document/scaffold writing a new adapter
  for any other harness.
- **Configurable hooks (`relay.toml`).** `relay.toml.example` (committed)
  documents `[general]` (page size/delay, reassemble window, hook page
  cap), `[generic]` (the harness-agnostic prefix + format), `[claude_code.<Event>]`
  (per-event enable + prefix + format, covering the documented Claude Code hook
  event set — see `adapters/claude-code.sh`'s header for the full list and
  which are actually wired vs. available to wire), and `[commands.<name>]`
  (in-chat commands). Every script falls back to its pre-existing
  env-var/hardcoded default with no `relay.toml` present — the
  backward-compat guarantee.
- **Configurable hooks for all abilities (complete).** `adapters/claude-code.sh`
  now handles ALL 30 documented Claude Code hook events (not just the
  original 11), each with its own `[claude_code.<Event>]`
  `enabled`/`prefix`/`format` in `relay.toml` — `lib/claude-code-events.sh`
  is the single source of truth for the event list + each event's
  install-time default (five low-volume lifecycle events default on, the
  other 25 are opt-in; a genuinely unrecognized/future event still
  defaults on, preserving the original universal-default contract).
  `install-hooks.sh` (+ `--uninstall`) is the one-command,
  idempotent/merge-not-clobber sync from `relay.toml`'s `enabled` set into
  `~/.claude/settings.json`'s `hooks.<Event>` entries — `jq`-driven,
  never destroys another tool's hook entry on the same event, never
  writes invalid JSON. `[generic].format` gives the harness-agnostic path
  (`relay-notify.sh`, and any adapter calling it via `--label`) the same
  templating engine (`lib/relay-common.sh`'s `render_template`, shared
  across all three config surfaces). All additive; an existing `relay.toml`
  (or none) behaves byte-for-byte as before.
- **In-chat commands (user→agent).** `tg-poll.sh` recognizes a leading
  `/slash` command OR a configured keyword prefix (`status ...`) from
  `relay.toml`'s `[commands.*]` tables and tags the emitted event
  (`[telegram:cmd:<tag>] ...`); an unmatched message (or no `relay.toml`
  at all) still emits the plain `[telegram] <text>` it always has.
- **Relay-handled-command SEAM.** `dispatch_command()` in `tg-poll.sh`
  routes a command by its `relay.toml` `mode`: `"forward"` (default) tags
  + emits to the agent as above; `"relay"` runs a local `handler` script
  instead and emits nothing to the agent — zero model tokens. Four real
  handlers now use this seam (see the metrics-dashboard entry below); see
  `handlers/README.md` for the contract and `handlers/example-echo.sh`
  (test-only) for the minimal shape one takes.
- **Metrics event hook point.** `lib/relay-common.sh`'s
  `emit_metric <source> <event> [detail]` appends a TSV line
  (epoch/source/event/detail) to `.metrics.log` on every
  send/poll-flush/hook/command/poll-error — non-blocking, best-effort,
  gitignored. Wired into `tg-send.sh`, `tg-poll.sh` (flush, both
  command-dispatch paths, and the two `getUpdates` failure paths —
  `poll_error`), `relay-notify.sh`, and `adapters/claude-code.sh`.
- **The relay-side metrics dashboard (zero model tokens).** Four real
  `handlers/*.sh` commands, all registered in `relay.toml.example`:
  `/dashboard` (a multi-panel dashboard — header stats, volume-over-time,
  hook-event breakdown, command usage — rendered as a dark-friendly
  matplotlib PNG via `sendPhoto`, with a graceful unicode/text fallback
  via `sendMessage` when matplotlib is unavailable; never fails to send
  something), `/stats` (the key numbers only, lighter/text-only),
  `/uptime` (poll-daemon uptime — real process elapsed time, or an
  honestly-labeled `.metrics.log` proxy if no process is found), and
  `/help` (lists every configured command live from `relay.toml`, never
  stale). The aggregation (`lib/metrics_agg.py`) is pure/stdlib-only and
  unit-tested independently of matplotlib (`tests/test_metrics_agg.py`);
  the image renderer (`lib/dashboard_render.py`) imports it and degrades
  to the same text renderer on any render failure. "Model-turns avoided"
  is tracked as a `Declared` heuristic (enabled-hook pings +
  relay-handled commands), never presented as a measured token count.
- **Self-hosted TTS voice messages (v0.2.0).** `lib/tts.sh` + `[tts]` in
  `relay.toml`: `tg-send.sh` can generate a voice note locally (piper
  preferred, espeak-ng fallback — no external TTS API, ever) and send it
  via `sendVoice`, either alongside the text (`mode = "text+voice"`) or
  instead of it (`mode = "voice-only"`, with an automatic text fallback
  if TTS is unavailable — a message is never dropped). `max_chars`
  (default 600) keeps long reports/dashboards text-only; a paginated
  multi-message send always skips TTS. `ffmpeg` transcodes WAV → OGG/OPUS
  for `sendVoice`, falling back to `sendAudio` with the raw WAV if
  `ffmpeg` is absent. Default `mode = "off"` — byte-identical behavior
  with no `relay.toml`, or an existing one that doesn't set `[tts]`;
  skip-graceful (one `emit_metric` line, never a failed/blocked send) at
  every stage where an engine/model/`ffmpeg` is missing.
- **Piper voice upgrade — pitch + cadence knobs, checksum-verified voice
  fetch (v0.2.1).** `fetch-voices.sh` downloads a recommended piper voice
  (`.onnx` + `.onnx.json`, sha256-verified, skip-graceful) with
  `en_US-joe-medium` (deep male) as the default. `[tts].length_scale`
  wires piper's own `--length-scale` cadence control straight through
  (lower = faster); `[tts].pitch` adds an optional, duration-preserving
  ffmpeg pitch shift (signed semitones, `asetrate` + `atempo`) on top of
  the chosen voice model. Both knobs are unset/off by default —
  byte-identical behavior with no `relay.toml`, or an existing one that
  doesn't set them. This bridge's own tuned, approved-final
  recommendation is `en_US-joe-medium` + `length_scale = "0.81"` with
  `pitch` left off — the cadence tweak alone was judged sufficient.
- **Structured formatting (v0.3.0, headline feature).** `lib/format.sh` +
  `[format]` in `relay.toml`: every outbound send (`tg-send.sh`'s single
  choke point, so every handler/adapter/hook path that routes through it)
  is run through a phone-readability layer instead of arriving as a wall
  of text — dynamic soft-wrap at word boundaries (never mid-word/URL/code
  span), bolded section headers (`## Header`, or a leading-emoji short
  all-caps/Title-Case line), real code boxes for fenced blocks
  (`` ```lang ``` `` → `<pre><code class="language-lang">`, verbatim —
  never reflowed; `myc`/`mycelium` are first-class tags, both normalizing
  to `language-mycelium`), inline `` `code` `` spans, expandable
  blockquotes for long `> quoted` text, and light `*emphasis*`/`_italic_`
  (word-boundary-guarded against snake_case false positives) — all via
  Telegram's `parse_mode=HTML` (far safer than MarkdownV2: only `< > &`
  need escaping). The **one deliberate exception** to this bridge's
  "byte-identical with no `relay.toml`" guarantee: `[format]` is **ON by
  default**, meant to just work; `enabled = false` (or `parse_mode =
  "none"`) restores the exact pre-v0.3.0 plain-text behavior. Never-silent
  at two layers: `format_message()` self-checks the HTML it renders (an
  open/close tag-balance check) and falls back to escaped plain text on
  any failure; `tg-send.sh` itself retries a Telegram-side HTML-parse
  rejection once as plain text — a message is never dropped nor sent with
  broken markup, and every fallback is logged via `.metrics.log`.
- **Opt-in token usage dashboard (v0.4.0).** `lib/usage_ingest.py` +
  `[usage]` in `relay.toml`: an OPT-IN (`enabled = false` by default),
  best-effort token-usage aggregation by **provider** (inferred from the
  model id), **model**, and **project**, over a harness's local
  session-transcript logs. A **source-adapter abstraction**
  (`[usage].source`) ships one concrete adapter today — Claude Code's own
  `~/.claude/projects/**/*.jsonl` — so other harnesses can point
  `[usage].projects_dir` at their own compatible transcript directory
  without a caller changing. `lib/dashboard_render.py` gained token-by-
  model/provider(share)/project bars plus an over-time trend (when
  timestamps allow), appended to `/dashboard` when `[usage].enabled =
  true` and available standalone via the new `/usage` command
  (`handlers/usage.sh`) — same image-with-text-fallback, never-fails-to-
  answer contract as `/dashboard`. Window is configurable (`today` /
  `all` / `<N>d` / `<N>h`, default `7d`), with `[usage].providers`/
  `.models` display toggles. **Privacy is load-bearing:** the aggregate
  cache is written under a gitignored `.usage/` (plus `*.usage.json` and
  `usage-cache/*` patterns), never committed, and never transmitted
  anywhere but this relay's own allowlisted Telegram chat; test fixtures
  are synthetic/fabricated data only, never real usage.
- **Syntax-highlighted code — inline alias + opt-in HTML document (v0.5.0,
  headline feature).** `lib/code_highlight.sh` + `lib/code_highlight.py` +
  `[code_highlight]` in `relay.toml`: EXTENDS `lib/format.sh`'s fenced-code
  handling (reuses its exact fence regex, never a second, possibly-drifting
  fence detector), additive to — never a replacement for — the
  always-on inline `<pre><code class="language-X">` box. **The hard
  constraint:** Telegram message text supports no color at all (a fixed
  HTML entity set; `<pre>`/`<code>` can't even nest `<b>`/`<i>` around
  individual tokens), so true per-token colored highlighting inside a chat
  bubble is structurally impossible as text, full stop. Two tiers ship
  today: (1) **the inline box, now Mycelium-aware** —
  `[code_highlight].myc_inline_lang` (default `"rust"`, applies
  UNCONDITIONALLY regardless of `mode`) aliases a `myc`/`mycelium` fence's
  inline class from the literal `language-mycelium` (which no Telegram
  client highlighter recognizes) to `language-rust` (Rust-family syntax
  aligns closely enough — `fn`/`let`/`match`/`impl`/strings/comments/
  generic types all color correctly; only Mycelium-unique keywords render
  as plain identifiers, never actively wrong) — zero-infra color on stock
  clients, today; (2) **an opt-in, host-highlighted HTML document** —
  `[code_highlight] mode = "html-doc"` additionally renders each fenced
  block to a SELF-CONTAINED HTML file (`pygments`' `HtmlFormatter(full=True,
  noclasses=True)` — every color inlined as CSS, no external stylesheet,
  no Pillow needed at all) and sends it via `sendDocument` — opened in the
  phone's browser, real per-token color on ANY device, and the code stays
  selectable/copyable in the document itself (unlike an image). A native
  `MyceliumLexer` (`RegexLexer`, `Declared`/best-effort — pygments ships no
  Mycelium lexer) covers the real keyword/type/comment/string/number
  surface and registers under both `myc` and `mycelium`, the same two
  first-class tags `lib/format.sh`'s `_fmt_known_lang` already treats
  specially; every other tag resolves via pygments' own `get_lexer_by_name`
  (hundreds of languages), with a plain-text-lexer fallback for an
  unrecognized/absent tag — always a clean document, never a crash.
  `mode = "inline-only"` (the DEFAULT) and `"off"` are behaviorally
  identical (the inline box only, this file stays a no-op) — a deliberate
  no-op default, since the always-on inline box is judged good-enough out
  of the box; `mode = "html-doc"` opts into the extra document.
  `[code_highlight].keep_text` — `"caption"` (default: a `<pre>` copy of
  the code as the document's caption when it fits Telegram's 1024-char cap,
  silently omitted rather than ever truncated when it doesn't — the inline
  box already carries the full code either way) or `"none"`. Never-silent:
  `pygments` absence, a block over `[code_highlight].max_lines` (never an
  unbounded document), or a genuine render error just means no document is
  sent for that ONE block — logged via `.metrics.log` — since the inline
  box already carries the code, nothing is ever dropped. Landed alongside a
  small batch of LOW-severity review fixes: `lib/format.sh` no longer emits
  a stray/empty `<pre></pre>` for an unclosed fence at EOF (falls back to
  literal text instead — a later, unrelated bare ` ``` ` line could
  previously open an empty fence with no real content); `handlers/usage.sh`
  and `handlers/dashboard.sh` swapped `mktemp -u` (a TOCTOU naming race) for
  plain `mktemp`; and `lib/format.sh` gained a direct unit test that forces
  `_fmt_html_balanced` to fail and asserts the escaped-plain-text fallback
  actually fires (previously reasoning-verified but untested).
- **Hook audio + guaranteed send ordering (v0.5.1, live-defect fixes).**
  Two maintainer-reported defects, fixed: (1) **automated hook pings had
  NO voice read-through**, ever, even with TTS on — `[tts].max_chars`
  (600) plus the pagination-always-skips-TTS rule silently gated out
  nearly every hook ping (they routinely carry a full agent message, so
  are routinely over max_chars/multi-page), while a short direct
  `tg-send.sh` call got voice fine. Fixed via a new send-origin tag:
  `adapters/claude-code.sh` exports `TG_SEND_SOURCE=hook` (a real env var,
  inherited straight through `relay-notify.sh`'s own call to
  `tg-send.sh`, no extra plumbing), and `tg-send.sh` gives a
  `hook`-tagged send its own, relaxed TTS-eligibility rule
  (`[tts].hook_voice`, default true) — eligible regardless of
  length/pagination; only the SPOKEN text is capped
  (`[tts].hook_voice_max_chars`, default 1500 — a sensible read-through,
  not the whole report; a truncation is logged, never silent). The
  never-silent contract is stronger for a hook ping than an ordinary
  send: text ALWAYS sends (every page, unabridged) even in
  `mode = "voice-only"` — voice is purely additive, never a replacement.
  (2) **message ordering wasn't guaranteed** — concurrent `tg-send.sh`
  invocations (a burst of hook events) each POST to Telegram
  independently, and network scheduling gives no cross-process ordering
  guarantee. Fixed with a serialized send queue: every send (dedup check
  through the final metric write) runs under an exclusive `flock` on
  `.tg-send.lock`, so concurrent invocations queue up and send one at a
  time — a send's text pages + voice note always complete before the
  next begins. A small, configurable delay (`[general].send_interval_ms`,
  default 350ms) is held after each send before the next may proceed —
  the maintainer explicitly accepted a slight delay to guarantee order.
  `flock` missing → skip-graceful (unserialized, exactly as before this
  feature existed, logged once) rather than a hard failure. Both features
  are fully backward-compatible: a direct/manual send (no
  `TG_SEND_SOURCE`) keeps the original TTS-eligibility rule unchanged,
  and `send_interval_ms = 0` (or no `flock`) reduces to today's behavior.
- **Clean spoken transcript — the voice reads prose, not symbols (v0.5.2,
  live-defect fix).** With v0.5.1 wiring voice onto hook pings, the TTS was
  reading the FORMATTING SYMBOLS aloud — `##` headers, `*`/`` ` ``/```` ``` ````
  markers, `<b>`/`<pre>`/`<code>` HTML tags, `&lt;`-style entities ("ampersand
  l t"), `>` quotes, `[k/n]` page headers, list bullets — because the spoken
  input was the same marked-up text as the message. Fixed with a
  strip-to-plain-prose step (`lib/tts.sh`'s `_tts_plain_text` →
  `lib/tts_plain_text.py`, stdlib-only, unit-tested) applied to the voice's
  input BEFORE piper/espeak sees it: it removes markdown + HTML markup and
  unescapes entities so the engine reads words. Per the maintainer's
  decision, **code and URLs are referenced, not read aloud** — a fenced or
  inline code span becomes a short spoken reference (`[tts].voice_code_ref`,
  default "code, see the text message"; `speak_code = true` opts back into
  reading code verbatim), a Markdown `[label](url)` reads as "label,
  `[tts].voice_link_ref`" (default "see the text message") and a bare URL as
  "link, <ref>", so the voice never spells out `h-t-t-p-s-colon-slash-slash…`.
  **The SENT text message keeps its full formatting** — only the voice's
  input is stripped; the text send is byte-for-byte unchanged. The v0.5.1
  `hook_voice_max_chars` cap now counts SPOKEN chars (applied after
  stripping). Skip-graceful: if the stripper is unavailable it falls back to
  the raw text (voice still speaks) rather than dropping the note.

## Next

- **More adapters.** A generic-webhook adapter (accepts any POSTed JSON on
  a local pipe/FIFO — still outbound-only, no listening port opened) and
  a plain-log-tail adapter (watches a file, forwards new lines) would
  cover most non-Claude-Code harnesses without a bespoke adapter each
  time.
- **Command → behavior wiring on the consuming side.** The relay tags a
  command (`[telegram:cmd:status]`); actually *acting* on it (e.g. a
  Claude Code session recognizing that tag and running a status check) is
  the consuming agent's job, not the relay's — documenting a reference
  pattern for that (a `Monitor` source's prompt/instructions noticing the
  tag) is worth adding once there's a real example to point to.
- **Per-project hook scoping.** SETUP.md's "Caveat: the hooks are global"
  still applies — narrowing `~/.claude/settings.json`'s hooks to a
  project-local `.claude/settings.json` (so only one repo's sessions get
  pinged) is a documented option, not yet a first-class flow.

## Principles

- Security: id-allowlist, token in local `.env` (gitignored,
  gitleaks-protected), outbound-only (no inbound port). `relay.toml`
  holds no secret — see `.gitignore`.
- Workspace-tools "productionize + publish" pattern: mocked/fixtured
  offline tests (`tests/`), MIT, public, reusable.
- Never-silent, honest; small auditable scripts (KISS). Every new surface
  (generic core, adapters, config, commands) is additive and
  backward-compatible — an existing invocation never silently changes
  behavior; a new default only ever applies when nothing configured it
  otherwise.

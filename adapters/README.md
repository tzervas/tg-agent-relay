# Adapters

An **adapter** turns one agent/harness's native event shape (a hook JSON
payload, a webhook body, a log line, whatever) into a call to
[`relay-notify.sh`](../relay-notify.sh) - the harness-neutral core that
actually formats, caps, and sends the message via
[`tg-send.sh`](../tg-send.sh).

TG Agent Relay ships these adapters today:

| Adapter | Harness | Wired via |
|---|---|---|
| [`claude-code.sh`](claude-code.sh) | Claude Code | `~/.claude/settings.json` hooks -> `hook-notify.sh` → this adapter |
| [`grok.sh`](grok.sh) | Grok Build (full 14-event provider) | `install-grok-hooks.sh` → `hook-notify-grok.sh` → this adapter → `lib/provider_hook.py` |
| [`backend-fifo-reader.sh`](backend-fifo-reader.sh) | Any (reader) | Backend FIFO for multi-backend `delivery = "fifo"` |

**Provider extensions** (hooks + usage + model labels) live under
[`providers/`](../providers/) — see [`docs/PROVIDERS.md`](../PROVIDERS.md).
Grok is fully implemented there; Claude usage/catalog is registered;
message formatting for Claude remains in `claude-code.sh` for now.

Multi-backend + project rooms: [`docs/ROUTING.md`](../docs/ROUTING.md).

If you use a different agent/harness, you don't need an adapter at all in
the simple case - call [`relay-notify.sh`](../relay-notify.sh) or
[`tg-send.sh`](../tg-send.sh) directly (see the repo README's "Generic
status input" section). Write a dedicated adapter when your harness emits
a **structured** event (JSON, a specific log format, ...) that's worth
parsing into a readable, per-event-type message the way `claude-code.sh`
does for Claude Code hooks - or when you want per-event-type
enable/format config the way `relay.toml`'s `[claude_code.*]` tables give
Claude Code events.

## Writing a new adapter

1. **Copy the stub.** [`generic-example.sh`](generic-example.sh) is a
   minimal, working template - copy it to `adapters/<your-harness>.sh` and
   fill in the two things it marks: how you read the event (stdin JSON,
   argv, a log line, ...) and how you turn it into one readable line.
2. **Parse your harness's event shape.** Use `jq` for JSON (already a
   dependency of this repo); for anything else, whatever's simplest -
   these are small, disposable scripts, not a framework.
3. **Hand off to `relay-notify.sh`, not `tg-send.sh` directly.** Two
   choices:
   - `"$BRIDGE_DIR/relay-notify.sh" --raw "$SUMMARY"` - you've already
     built the exact message text (with your own emoji/prefix baked in,
     the way `claude-code.sh` does); `--raw` sends it unmodified (after
     the huge-message cap) with no further formatting.
   - `"$BRIDGE_DIR/relay-notify.sh" --label "<event-name>" "<detail text>"`
     - let the generic `[generic]` config (an optional prefix) format it
       for you, if you'd rather not carry your own emoji-per-event table.
4. **Make it config-driven if it has more than one or two event types**
   (optional but recommended - see `claude-code.sh` for the pattern, which
   wires ALL of Claude Code's documented hook "abilities" this way):
   source `lib/relay-config.sh`, call `load_relay_config
   "$BRIDGE_DIR/relay.toml"`, then read `[<your-namespace>.<EventName>]`
   tables via `cfg_get` for three overrides per event:
   - `enabled` (`true`/`false`) - whether this event fires at all.
   - `prefix` - the leading emoji/marker for this event's default message.
   - `format` - a full `{placeholder}` message template for this event,
     rendered by `lib/relay-common.sh`'s `render_template` (the SAME
     function `claude-code.sh` and `relay-notify.sh`'s own `[generic]`
     section use - one substitution engine, three config surfaces). Build
     your default message text as a template string too (e.g. `'{prefix}
     ${VERB} {tool}'`) and pass it as `render_template`'s fallback when
     `format` is unset - that IS your backward-compat guarantee: the
     default template renders byte-identically to whatever your adapter's
     hardcoded text was before you added `format` support, and a user's
     custom `format` only ever kicks in when they set one.

   Document your namespace + event names + each event's available
   placeholders in `relay.toml.example` (a new commented-out section, same
   pattern as `[claude_code.*]`) so users can discover and toggle them.
   That is the full recipe for "wiring an ability through an adapter":
   one `[<namespace>.<ability-name>]` table per thing your harness can do,
   each with its own `enabled`/`prefix`/`format`, all going through the
   same `render_template` call shape.
5. **If your harness has an install-time hook-wiring step of its own**
   (the way Claude Code's hooks live in `~/.claude/settings.json`), look at
   `install-hooks.sh` (repo root) for the pattern: a small script that
   reads which of your adapter's events are `enabled` in `relay.toml` and
   idempotently, merge-not-clobber writes/removes the matching entries in
   your harness's own config file via `jq` - never a raw overwrite.
6. **Tag automated/unattended events with `TG_SEND_SOURCE=hook`** if your
   harness fires events without a human directly typing them (the way
   Claude Code's hooks do) - `TG_SEND_SOURCE=hook "$BRIDGE_DIR/relay-notify.sh"
   --raw "$SUMMARY"` (see `claude-code.sh`'s last line). It's a real
   environment variable, so it passes straight through relay-notify.sh's
   own call to `tg-send.sh` with no extra plumbing needed on either
   script's part. `tg-send.sh` uses it to give these pings a voice
   read-through even when long/paginated (`relay.toml [tts].hook_voice`) -
   see `tg-send.sh`'s header. Leave it unset for a send that's already
   effectively a direct/manual one (e.g. relaying a human-authored
   message) - direct sends keep the original, stricter TTS eligibility
   rule.
7. **Always exit 0** unless your harness genuinely needs the adapter's
   exit code to signal something back to it (Claude Code's `SubagentStop`
   hook is advisory - a nonzero exit there would *block* the subagent from
   stopping, which is why `claude-code.sh` never does that). Never let a
   notification failure disrupt the harness it's observing.
8. **Wire it into your harness's own hook/event config** pointing at your
   new adapter script (or, if your harness supports it, a shim script the
   way `hook-notify.sh` shims `claude-code.sh` - handy if the harness's
   config format is finicky about the exact command path and you want a
   stable indirection point).
9. **Test offline.** See `tests/` at the repo root - feed a sample event
   payload to your adapter with `tg-send.sh` swapped for a mock that
   records its argument instead of hitting the network (the pattern every
   existing test in `tests/` uses).

## `generic-example.sh`

A minimal stub: reads one line from stdin, wraps it as
`"<label>: <line>"`, and calls `relay-notify.sh --raw`. Copy it as your
starting point rather than writing an adapter from scratch.

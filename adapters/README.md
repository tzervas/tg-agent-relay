# Adapters

An **adapter** turns one agent/harness's native event shape (a hook JSON
payload, a webhook body, a log line, whatever) into a call to
[`relay-notify.sh`](../relay-notify.sh) - the harness-neutral core that
actually formats, caps, and sends the message via
[`tg-send.sh`](../tg-send.sh).

TG Agent Relay ships one adapter today:

| Adapter | Harness | Wired via |
|---|---|---|
| [`claude-code.sh`](claude-code.sh) | Claude Code | `~/.claude/settings.json` hooks -> `hook-notify.sh` (thin shim) -> this adapter |

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
   (optional but recommended - see `claude-code.sh` for the pattern):
   source `lib/relay-config.sh`, call `load_relay_config
   "$BRIDGE_DIR/relay.toml"`, then read `[<your-namespace>.<EventName>]`
   tables via `cfg_get` for `enabled`/`prefix` overrides. Document your
   namespace + event names in `relay.toml.example` (a new commented-out
   section, same pattern as `[claude_code.*]`) so users can discover and
   toggle them.
5. **Always exit 0** unless your harness genuinely needs the adapter's
   exit code to signal something back to it (Claude Code's `SubagentStop`
   hook is advisory - a nonzero exit there would *block* the subagent from
   stopping, which is why `claude-code.sh` never does that). Never let a
   notification failure disrupt the harness it's observing.
6. **Wire it into your harness's own hook/event config** pointing at your
   new adapter script (or, if your harness supports it, a shim script the
   way `hook-notify.sh` shims `claude-code.sh` - handy if the harness's
   config format is finicky about the exact command path and you want a
   stable indirection point).
7. **Test offline.** See `tests/` at the repo root - feed a sample event
   payload to your adapter with `tg-send.sh` swapped for a mock that
   records its argument instead of hitting the network (the pattern every
   existing test in `tests/` uses).

## `generic-example.sh`

A minimal stub: reads one line from stdin, wraps it as
`"<label>: <line>"`, and calls `relay-notify.sh --raw`. Copy it as your
starting point rather than writing an adapter from scratch.

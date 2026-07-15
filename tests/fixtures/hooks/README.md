# Hook payload fixtures (offline)

Synthetic JSON samples of harness hook stdin payloads. **No secrets**, no live
session data. Used by `tests/test_hook_fixtures.py` and as manual stdin for
adapter smoke checks.

## Layout

| Path | Adapter / consumer | Notes |
|------|--------------------|--------|
| `grok/*.json` | `adapters/grok.sh` → `providers/grok/hooks.py` via `lib/provider_hook.py` / `tg_agent_relay.hooks.dispatch_hook("grok", …)` | One file per documented Grok Build event (14). camelCase fields; `hookEventName` often snake/lowercase. |
| `claude/*.json` | `adapters/claude-code.sh` (shell adapter; Claude has no Python `format_hook` yet) | Representative events matching fields read by the adapter. |

## Grok (`grok/`)

| File | Default enabled | Typical `hookEventName` in fixture |
|------|-----------------|-------------------------------------|
| `SessionStart.json` | no | `session_start` |
| `UserPromptSubmit.json` | no | `user_prompt_submit` |
| `PreToolUse.json` | no | `pre_tool_use` |
| `PostToolUse.json` | no | `post_tool_use` |
| `PostToolUseFailure.json` | **yes** | `post_tool_use_failure` |
| `PermissionDenied.json` | no | `permission_denied` |
| `Stop.json` | **yes** | `stop` |
| `StopFailure.json` | **yes** | `stop_failure` |
| `Notification.json` | **yes** | `notification` |
| `SubagentStart.json` | no | `subagent_start` |
| `SubagentStop.json` | **yes** | `subagent_stop` |
| `PreCompact.json` | no | `pre_compact` |
| `PostCompact.json` | no | `post_compact` |
| `SessionEnd.json` | no | `SessionEnd` (PascalCase sample) |

Common Grok fields: `sessionId`, `cwd`, `workspaceRoot`, `toolName`,
`toolInput`, `toolUseId`, `message`, etc.

Manual check:

```bash
source lib/python.sh
cat tests/fixtures/hooks/grok/Stop.json | relay_python -m tg_agent_relay.cli hook grok
# or shell path:
cat tests/fixtures/hooks/grok/Stop.json | bash adapters/grok.sh
```

## Claude (`claude/`)

| File | Adapter | Fields exercised |
|------|---------|------------------|
| `SessionStart.json` | `adapters/claude-code.sh` | `source`, `model`, `session_title`, `agent_type` |
| `Notification.json` | `adapters/claude-code.sh` | `notification_type`, `message` |
| `SubagentStop.json` | `adapters/claude-code.sh` | `agent_type`, `last_assistant_message` |
| `Stop.json` | `adapters/claude-code.sh` | `last_assistant_message` |
| `PreToolUse.json` | `adapters/claude-code.sh` | `tool_name`, `tool_input` |
| `PostToolUseFailure.json` | `adapters/claude-code.sh` | `tool_name`, `error_message` |

All Claude fixtures use `hook_event_name` in PascalCase (e.g. `"Notification"`).

Manual check:

```bash
cat tests/fixtures/hooks/claude/Notification.json | bash adapters/claude-code.sh
```

## Offline unit tests

```bash
source lib/python.sh
relay_python tests/test_hook_fixtures.py
```

Grok fixtures are asserted via `dispatch_hook` / `format_hook`. Claude fixtures
are JSON-shape samples for the shell adapter (and future `providers/claude/hooks.py`).

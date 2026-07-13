"""Optional MCP-shaped facade over *our* Python send/list APIs (issue #37).

This is a **thin stub**, not a production MCP deploy and not a second
Telegram stack. Tool handlers call into ``tg_agent_relay`` (config,
routing, send) and ``lib/usage_ingest`` — never Telethon / user sessions.

Security invariants (unchanged from the Bot API relay):
  - Secrets remain ``BOT_TOKEN`` + allowlist ids (``ALLOWED_USER_ID``,
    ``ALLOWED_CHAT_ID``) from ``.env`` — no new secret types.
  - Outbound send still uses Bot API identity only.
  - Inbound allowlist is enforced by ``tg-poll`` / future poll port; this
    facade does not open a second trust boundary.
  - Default ``dry_run=True`` so listing tools / local call_tool never hits
    the network unless the operator opts in (``dry_run=false`` + token).

MCP surface (JSON-RPC shaped, stdio-friendly):
  - ``initialize`` / ``ping``
  - ``tools/list`` → schema for relay_send, relay_list_projects,
    relay_usage_summary
  - ``tools/call`` → dispatch to Python modules above

Full MCP SDK server packaging is out of scope for this issue.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tg_agent_relay import __version__
from tg_agent_relay.config import load_config
from tg_agent_relay.protocols import SendRequest
from tg_agent_relay.routing import lookup_chat

# --- MCP tool schemas (tools/list) ------------------------------------------

SERVER_NAME = "tg-agent-relay"
SERVER_INSTRUCTIONS = (
    "Thin MCP facade over tg-agent-relay Bot API modules. "
    "Uses BOT_TOKEN + allowlist only — no Telethon user sessions. "
    "Default dry_run avoids live Telegram until explicitly disabled."
)

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "relay_send",
        "description": (
            "Send a message via the relay Bot API path (EnvSender / send_message). "
            "Resolves chat from backend+project when provided. "
            "Default dry_run=true returns the planned send without network I/O."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Message body to send",
                },
                "backend": {
                    "type": "string",
                    "description": "Optional backend id for route_lookup_chat",
                },
                "project": {
                    "type": "string",
                    "description": "Optional project slug for route_lookup_chat",
                },
                "chat_id": {
                    "type": "string",
                    "description": "Optional explicit Telegram chat id (overrides lookup)",
                },
                "thread_id": {
                    "type": "string",
                    "description": "Optional forum topic / message_thread_id",
                },
                "parse_mode": {
                    "type": "string",
                    "description": "Optional Telegram parse_mode (e.g. HTML)",
                    "default": "",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true (default), do not call Telegram — return plan only",
                    "default": True,
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "relay_list_projects",
        "description": (
            "List project slugs and roots from relay.toml [projects] "
            "(plus project labels seen on chat bindings). Local config only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_path": {
                    "type": "string",
                    "description": "Optional path to relay.toml (default: bridge relay.toml)",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "relay_usage_summary",
        "description": (
            "Aggregate local token-usage transcripts via lib/usage_ingest.collect. "
            "Never calls Telegram; reads local harness logs only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": 'Usage adapter: "claude-code", "grok", "multi", "auto", …',
                    "default": "auto",
                },
                "projects_dir": {
                    "type": "string",
                    "description": "Path for the adapter (ignored for multi/auto defaults)",
                    "default": "",
                },
                "window": {
                    "type": "string",
                    "description": 'Window: "today" | "all" | "lifetime" | "<N>h|d|w|m|y"',
                    "default": "today",
                },
            },
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = frozenset(t["name"] for t in TOOL_DEFINITIONS)


def list_tools() -> list[dict[str, Any]]:
    """Return MCP tools/list ``tools`` array (schemas only; no network)."""
    return [dict(t) for t in TOOL_DEFINITIONS]


def tools_list_result() -> dict[str, Any]:
    """Full tools/list result object."""
    return {"tools": list_tools()}


def _text_content(text: str, *, is_error: bool = False) -> dict[str, Any]:
    """MCP tools/call content block + structured payload helpers."""
    out: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }
    return out


def _json_content(payload: Any, *, is_error: bool = False) -> dict[str, Any]:
    return _text_content(json.dumps(payload, indent=2, default=str), is_error=is_error)


@dataclass
class McpFacade:
    """In-process MCP tool dispatcher over package modules.

    ``dry_run`` defaults True so unit tests and casual ``tools/call`` never
    POST to api.telegram.org. Set dry_run=False (and provide BOT_TOKEN) for
    a live send through EnvSender.
    """

    bridge_dir: Path | str | None = None
    cfg: dict[str, Any] | None = None
    dry_run: bool = True
    _cfg_cache: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.bridge_dir is not None:
            self.bridge_dir = Path(self.bridge_dir)
        if self.cfg is not None:
            self._cfg_cache = dict(self.cfg)

    def _root(self) -> Path:
        if self.bridge_dir is not None:
            return Path(self.bridge_dir)
        return Path(__file__).resolve().parents[1]

    def _load_cfg(self, config_path: str | None = None) -> dict[str, Any]:
        if config_path:
            return load_config(config_path, bridge_dir=self._root())
        if self.cfg is not None:
            return self._cfg_cache
        if self._cfg_cache:
            return self._cfg_cache
        self._cfg_cache = load_config(bridge_dir=self._root())
        return self._cfg_cache

    def list_tools(self) -> list[dict[str, Any]]:
        return list_tools()

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Dispatch a tools/call. Returns MCP-shaped content result."""
        args = dict(arguments or {})
        if name not in TOOL_NAMES:
            return _text_content(f"unknown tool: {name!r}", is_error=True)
        if name == "relay_send":
            return self._tool_relay_send(args)
        if name == "relay_list_projects":
            return self._tool_relay_list_projects(args)
        if name == "relay_usage_summary":
            return self._tool_relay_usage_summary(args)
        return _text_content(f"unhandled tool: {name!r}", is_error=True)

    def _tool_relay_send(self, args: dict[str, Any]) -> dict[str, Any]:
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            return _text_content(
                "relay_send: 'text' is required and must be non-empty", is_error=True
            )

        backend = str(args.get("backend") or "")
        project = str(args.get("project") or "")
        chat_id = str(args.get("chat_id") or "")
        thread_id = str(args.get("thread_id") or "")
        parse_mode = str(args.get("parse_mode") or "")
        # Per-call dry_run overrides facade default; missing → facade default
        dry = bool(args["dry_run"]) if "dry_run" in args else self.dry_run

        if not chat_id and (backend or project):
            cfg = self._load_cfg()
            chat_id, looked_thread = lookup_chat(cfg, backend, project)
            if not thread_id and looked_thread:
                thread_id = looked_thread

        if not chat_id:
            # Fall back to ALLOWED_CHAT_ID from env loader (no network).
            from tg_agent_relay.send import load_env

            env = load_env(self._root())
            chat_id = env.get("ALLOWED_CHAT_ID") or ""

        plan = {
            "tool": "relay_send",
            "dry_run": dry,
            "text": text,
            "backend": backend,
            "project": project,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "parse_mode": parse_mode,
        }

        if dry:
            plan["status"] = "dry_run"
            plan["note"] = "No Telegram API call (stub dry_run). Set dry_run=false to send."
            return _json_content(plan)

        if not chat_id:
            return _text_content(
                "relay_send: no chat_id (set chat_id, backend+project, or ALLOWED_CHAT_ID)",
                is_error=True,
            )

        from tg_agent_relay.send import EnvSender

        sender = EnvSender(bridge_dir=self._root())
        if not sender.token:
            return _text_content(
                "relay_send: BOT_TOKEN missing — refuse live send (allowlist/token model intact)",
                is_error=True,
            )
        req = SendRequest(
            text=text,
            chat_id=chat_id,
            thread_id=thread_id,
            parse_mode=parse_mode,
            backend=backend,
            project=project,
            source="mcp",
        )
        sender.send(req)
        plan["status"] = "sent"
        plan["note"] = "Dispatched via EnvSender (Bot API). Inbound allowlist still tg-poll only."
        return _json_content(plan)

    def _tool_relay_list_projects(self, args: dict[str, Any]) -> dict[str, Any]:
        config_path = args.get("config_path")
        path = str(config_path) if config_path else None
        cfg = self._load_cfg(path)
        projects_cfg = cfg.get("projects") or {}
        projects: list[dict[str, Any]] = []
        if isinstance(projects_cfg, dict):
            for slug, meta in projects_cfg.items():
                entry: dict[str, Any] = {"slug": str(slug)}
                if isinstance(meta, dict):
                    if "root" in meta:
                        entry["root"] = meta.get("root")
                    if "default_backend" in meta:
                        entry["default_backend"] = meta.get("default_backend")
                projects.append(entry)

        # Also surface project labels bound on chats but missing from [projects]
        seen = {p["slug"] for p in projects}
        chats = cfg.get("chats") or []
        if isinstance(chats, list):
            for c in chats:
                if not isinstance(c, dict):
                    continue
                slug = c.get("project")
                if slug and str(slug) not in seen:
                    projects.append({"slug": str(slug), "from_chat_binding": True})
                    seen.add(str(slug))

        payload = {
            "tool": "relay_list_projects",
            "count": len(projects),
            "projects": projects,
        }
        return _json_content(payload)

    def _tool_relay_usage_summary(self, args: dict[str, Any]) -> dict[str, Any]:
        source = str(args.get("source") or "auto")
        projects_dir = str(args.get("projects_dir") or "")
        window = str(args.get("window") or "today")

        # Local import: usage lives under lib/ (stdlib path insert).
        lib = self._root() / "lib"
        if str(lib) not in sys.path:
            sys.path.insert(0, str(lib))
        try:
            import usage_ingest as u  # type: ignore
        except ImportError as exc:
            return _text_content(
                f"relay_usage_summary: cannot import usage_ingest: {exc}", is_error=True
            )

        summary = u.collect(source, projects_dir, window)
        # Keep response bounded: drop huge nested tables if present later; for
        # now return the full honest summary dict (local-only, no network).
        payload = {
            "tool": "relay_usage_summary",
            "summary": summary,
        }
        return _json_content(payload)

    # --- JSON-RPC (minimal MCP-shaped) --------------------------------------

    def handle_jsonrpc(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Handle one JSON-RPC 2.0 request. Returns None for notifications."""
        if not isinstance(request, dict):
            return _rpc_error(None, -32600, "Invalid Request")

        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        # Notifications have no id (or null) and get no response.
        is_notification = "id" not in request

        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": __version__,
                },
                "instructions": SERVER_INSTRUCTIONS,
            }
            return None if is_notification else _rpc_result(req_id, result)

        if method in ("notifications/initialized", "initialized"):
            return None

        if method == "ping":
            return None if is_notification else _rpc_result(req_id, {})

        if method == "tools/list":
            return None if is_notification else _rpc_result(req_id, tools_list_result())

        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            result = self.call_tool(name, arguments)
            return None if is_notification else _rpc_result(req_id, result)

        if is_notification:
            return None
        return _rpc_error(req_id, -32601, f"Method not found: {method!r}")


def _rpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


def main(argv: list[str] | None = None) -> int:
    """CLI: print tools/list JSON, or run a single-line JSON-RPC stdio loop.

    Usage:
      python -m tg_agent_relay.mcp_stub              # dump tools/list
      python -m tg_agent_relay.mcp_stub --stdio     # read JSON-RPC lines from stdin
    """
    args = list(argv if argv is not None else sys.argv[1:])
    facade = McpFacade(dry_run=True)

    if "--stdio" in args:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                print(
                    json.dumps(_rpc_error(None, -32700, "Parse error")),
                    flush=True,
                )
                continue
            resp = facade.handle_jsonrpc(req)
            if resp is not None:
                print(json.dumps(resp), flush=True)
        return 0

    # Default: show tool schemas (offline, no network).
    print(json.dumps(tools_list_result(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

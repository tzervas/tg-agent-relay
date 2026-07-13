#!/usr/bin/env python3
"""lib/provider_hook.py - Dispatch harness hook JSON through a provider extension.

CLI (used by adapters/grok.sh and future thin adapters):
  provider_hook.py <provider_id> [--config-json PATH] [--emit-meta]

Reads hook payload from stdin (JSON). Prints one line to stdout:
  SKIP:<reason>     — event disabled or empty
  OK:<summary>      — formatted one-line summary for relay-notify --raw

Optional --emit-meta prints a second line:
  META:backend=… project_hint=…

Environment (Grok runner etc.) is merged into the payload for field lookup:
  GROK_HOOK_EVENT, GROK_SESSION_ID, GROK_WORKSPACE_ROOT, CLAUDE_PROJECT_DIR

Config overrides (enabled/prefix/format) come from optional JSON file:
  { "grok": { "Stop": { "enabled": true, "prefix": "🏁", "format": "…" } } }
Shell adapters build this from relay.toml via jq when available.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_providers():
    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import providers  # noqa: F401
    from providers.base import get_provider

    return get_provider


def _merge_env_payload(payload: dict) -> dict:
    out = dict(payload)
    # Grok env
    if not out.get("hookEventName") and not out.get("hook_event_name"):
        if os.environ.get("GROK_HOOK_EVENT"):
            out["hookEventName"] = os.environ["GROK_HOOK_EVENT"]
    if not out.get("sessionId") and os.environ.get("GROK_SESSION_ID"):
        out["sessionId"] = os.environ["GROK_SESSION_ID"]
    if not out.get("cwd") and not out.get("workspaceRoot"):
        wr = os.environ.get("GROK_WORKSPACE_ROOT") or os.environ.get("CLAUDE_PROJECT_DIR")
        if wr:
            out["workspaceRoot"] = wr
            out["cwd"] = wr
    return out


def _event_opts(config: dict, namespace: str, event: str, provider) -> dict[str, str]:
    block = ((config.get(namespace) or {}).get(event) or {})
    if not isinstance(block, dict):
        block = {}
    enabled = block.get("enabled")
    if enabled is None:
        en = provider.default_enabled(event)
    else:
        en = str(enabled).lower() in ("true", "1", "yes", "on")
    prefix = block.get("prefix") or provider.default_prefix(event)
    fmt = block.get("format") or ""
    return {"enabled": "true" if en else "false", "prefix": str(prefix), "format": str(fmt)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("provider_id", help="Provider id: grok | claude | …")
    p.add_argument("--config-json", default="", help="Optional JSON overrides from relay.toml")
    p.add_argument("--emit-meta", action="store_true")
    p.add_argument("--list-events", action="store_true", help="List provider hook events as JSON")
    args = p.parse_args(argv)

    get_provider = _load_providers()
    provider = get_provider(args.provider_id)
    if provider is None:
        print(f"SKIP:unknown provider {args.provider_id!r}", file=sys.stdout)
        return 0

    if args.list_events:
        data = [
            {
                "name": e.name,
                "default_enabled": e.default_enabled,
                "default_prefix": e.default_prefix,
                "description": e.description,
                "placeholders": list(e.placeholders),
            }
            for e in provider.hook_events
        ]
        print(json.dumps({"provider": provider.id, "events": data}, indent=2))
        return 0

    raw = sys.stdin.read()
    if not raw.strip():
        print("SKIP:empty payload")
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("SKIP:invalid json")
        return 0
    if not isinstance(payload, dict):
        print("SKIP:payload not object")
        return 0

    payload = _merge_env_payload(payload)
    raw_event = (
        payload.get("hookEventName")
        or payload.get("hook_event_name")
        or os.environ.get("GROK_HOOK_EVENT")
        or "unknown"
    )
    norm = provider.normalize_event(str(raw_event)) if provider.normalize_event else str(raw_event)

    config: dict = {}
    if args.config_json and Path(args.config_json).is_file():
        try:
            config = json.loads(Path(args.config_json).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            config = {}

    opts = _event_opts(config, provider.config_namespace, norm, provider)
    if opts["enabled"] != "true":
        print(f"SKIP:disabled:{norm}")
        return 0

    if provider.format_hook is None:
        print(f"SKIP:no_format_hook:{provider.id}")
        return 0

    summary = provider.format_hook(payload, norm, opts)
    if not summary:
        print("SKIP:empty_summary")
        return 0

    print(f"OK:{summary}")
    if args.emit_meta:
        cwd = payload.get("cwd") or payload.get("workspaceRoot") or ""
        print(f"META:backend={provider.backend_id} event={norm} cwd={cwd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

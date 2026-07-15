"""Hook dispatch facade — wraps lib/provider_hook for package use."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _ensure_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    lib = root / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    return root


def dispatch_hook(
    provider_id: str,
    payload: dict[str, Any] | str,
    *,
    config: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Run provider hook formatting.

    Returns (status, body) where status is 'OK' | 'SKIP' and body is summary
    or reason. Mirrors provider_hook.py stdout contract without subprocess.
    """
    _ensure_path()
    import providers  # noqa: F401
    from providers.base import get_provider

    provider = get_provider(provider_id)
    if provider is None:
        return "SKIP", f"unknown provider {provider_id!r}"

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return "SKIP", "invalid json"
    if not isinstance(payload, dict):
        return "SKIP", "payload not object"

    # Merge Grok env like provider_hook
    if (
        not payload.get("hookEventName")
        and not payload.get("hook_event_name")
        and os.environ.get("GROK_HOOK_EVENT")
    ):
        payload = {**payload, "hookEventName": os.environ["GROK_HOOK_EVENT"]}
    wr = os.environ.get("GROK_WORKSPACE_ROOT") or os.environ.get("CLAUDE_PROJECT_DIR")
    if not payload.get("cwd") and not payload.get("workspaceRoot") and wr:
        payload = {**payload, "cwd": wr, "workspaceRoot": wr}

    raw_event = (
        payload.get("hookEventName")
        or payload.get("hook_event_name")
        or os.environ.get("GROK_HOOK_EVENT")
        or "unknown"
    )
    norm = provider.normalize_event(str(raw_event)) if provider.normalize_event else str(raw_event)

    cfg = config or {}
    block = (cfg.get(provider.config_namespace) or {}).get(norm) or {}
    if not isinstance(block, dict):
        block = {}
    enabled = block.get("enabled")
    if enabled is None:
        en = provider.default_enabled(norm)
    else:
        en = str(enabled).lower() in ("true", "1", "yes", "on")
    if not en:
        return "SKIP", f"disabled:{norm}"

    if provider.format_hook is None:
        return "SKIP", f"no_format_hook:{provider.id}"

    prefix = str(block.get("prefix") or provider.default_prefix(norm))
    fmt = str(block.get("format") or "")
    summary = provider.format_hook(payload, norm, {"prefix": prefix, "format": fmt})
    if not summary:
        return "SKIP", "empty_summary"
    return "OK", summary

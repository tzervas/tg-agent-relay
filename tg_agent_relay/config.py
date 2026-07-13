"""Load relay.toml + .chats.d overlay into a dict (stdlib)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_toml_as_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import tomllib
    except ImportError:
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
        return data if isinstance(data, dict) else {}
    except OSError:
        return {}


def _merge_chats_overlay(cfg: dict[str, Any], overlay_path: Path) -> dict[str, Any]:
    if not overlay_path.is_file():
        return cfg
    try:
        raw = json.loads(overlay_path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return cfg
    over = raw.get("chats", raw) if isinstance(raw, dict) else raw
    if not isinstance(over, list):
        return cfg
    base = list(cfg.get("chats") or [])
    if not isinstance(base, list):
        base = []

    def key(c: dict) -> str:
        return f"{c.get('chat_id', '')}|{c.get('thread_id', '') or ''}"

    okeys = {key(c) for c in over if isinstance(c, dict)}
    kept = [c for c in base if isinstance(c, dict) and key(c) not in okeys]
    out = dict(cfg)
    out["chats"] = kept + [c for c in over if isinstance(c, dict)]
    return out


def load_config(
    relay_toml: Path | str | None = None,
    *,
    bridge_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Load full effective config (toml + chat overlay)."""
    root = Path(bridge_dir) if bridge_dir else _repo_root()
    toml_path = Path(relay_toml) if relay_toml else root / "relay.toml"
    cfg = load_toml_as_dict(toml_path)
    overlay = Path(os.environ.get("RELAY_CHATS_OVERLAY", root / ".chats.d" / "bindings.json"))
    return _merge_chats_overlay(cfg, overlay)


def cfg_get(cfg: dict[str, Any], dotted: str, default: Any = None) -> Any:
    """Dotted path get: 'routing.default_backend' or 'backends.claude.tag'."""
    cur: Any = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return default if cur is None else cur

#!/usr/bin/env python3
"""Safe Telegram remote config: allowlisted get/set on relay.toml.

Used by handlers/config.sh (zero model tokens). Never reads or writes
secrets (.env / BOT_TOKEN / ALLOWED_*). Fail closed on unknown keys.

Reads via stdlib ``tomllib`` only. Writes via the built-in ``dump_toml``
encoder (no third-party TOML writer); run with bridge ``PYTHONPATH`` from
``lib/exec-env.sh`` so ``remote_config`` resolves under ``lib/``.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]

# --- Allowlist: dotted key -> kind (bool | int | str | enum:...) ---
_BUILTIN_ALLOW: dict[str, str] = {
    "usage.enabled": "bool",
    "usage.source": "str",
    "usage.window": "window",
    "usage.providers": "bool",
    "usage.models": "bool",
    "usage.charts.default": "chart",
    "dashboard.window_hours": "int",
    "general.page_size": "int",
    "general.send_interval_ms": "int",
    "format.wrap_width": "int",
    "format.enabled": "bool",
    "tts.mode": "tts_mode",
    "tts.hook_voice": "bool",
    "routing.require_prefix": "bool",
}

_ALLOT_RE = re.compile(r"^usage\.allotments\.[a-zA-Z0-9][a-zA-Z0-9_-]*\.[a-zA-Z0-9][a-zA-Z0-9_-]*$")

_SECRET_KEY_RE = re.compile(
    r"(bot[_-]?token|allowed[_-]?(user|chat)[_-]?id|secret|password|api[_-]?key)",
    re.I,
)

_CHART_VALUES = frozenset({"bar", "line", "both"})
_TTS_MODES = frozenset({"off", "text+voice", "voice-only"})
_WINDOW_RE = re.compile(
    r"^(today|all|lifetime|[0-9]+[hdwmy])$",
    re.I,
)

_RESTART_HINTS: dict[str, str] = {
    "routing.require_prefix": "Takes effect on the next poll loop (relay.toml is reloaded each cycle).",
    "general.page_size": "Applies to the next outbound send (tg-send).",
    "general.send_interval_ms": "Applies to the next outbound send (tg-send).",
    "format.enabled": "Applies to the next outbound send.",
    "format.wrap_width": "Applies to the next outbound send.",
    "tts.mode": "Applies to the next voice-capable send.",
    "tts.hook_voice": "Applies to the next hook voice send.",
    "usage.enabled": "Applies to the next /usage or /dashboard usage panel.",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_toml(path: Path) -> dict[str, Any]:
    if tomllib is None or not path.is_file():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, tomllib.TOMLDecodeError) as _exc:
        del _exc
        return {}


def _extra_allow(cfg: dict[str, Any]) -> set[str]:
    remote = cfg.get("config", {})
    if not isinstance(remote, dict):
        return set()
    raw = remote.get("allow") or remote.get("extra_allow") or []
    if isinstance(raw, str):
        return {raw.strip()} if raw.strip() else set()
    if isinstance(raw, list):
        return {str(x).strip() for x in raw if str(x).strip()}
    return set()


def _effective_allow(cfg: dict[str, Any]) -> set[str]:
    out = set(_BUILTIN_ALLOW)
    out.update(_extra_allow(cfg))
    return out


def _key_kind(key: str, allow: set[str]) -> str | None:
    if key in allow:
        return _BUILTIN_ALLOW.get(key, "str")
    if _ALLOT_RE.match(key):
        return "number"
    return None


def is_key_allowed(key: str, cfg: dict[str, Any] | None = None) -> bool:
    key = key.strip()
    if not key or ".." in key or key.startswith(".") or key.endswith("."):
        return False
    if _SECRET_KEY_RE.search(key.replace(".", "_")):
        return False
    if re.search(r"[/\\]|\\.\\.", key):
        return False
    allow = _effective_allow(cfg or {})
    if key in allow:
        return True
    return bool(_ALLOT_RE.match(key))


def _get_nested(data: dict[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set_nested(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur: dict[str, Any] = data
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _parse_bool(raw: str) -> bool | None:
    s = raw.strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return None


def _coerce_value(kind: str, raw: str) -> Any:
    s = raw.strip()
    if kind == "bool":
        b = _parse_bool(s)
        if b is None:
            raise ValueError(f"expected true/false, got {raw!r}")
        return b
    if kind == "int":
        if not re.fullmatch(r"-?[0-9]+", s):
            raise ValueError(f"expected integer, got {raw!r}")
        return int(s)
    if kind == "number":
        if re.fullmatch(r"-?[0-9]+", s):
            return int(s)
        if re.fullmatch(r"-?[0-9]+(\.[0-9]+)?", s):
            return float(s)
        raise ValueError(f"expected number, got {raw!r}")
    if kind == "chart":
        v = s.lower()
        if v not in _CHART_VALUES:
            raise ValueError(f"expected bar|line|both, got {raw!r}")
        return v
    if kind == "tts_mode":
        if s not in _TTS_MODES:
            raise ValueError(f"expected one of {sorted(_TTS_MODES)}, got {raw!r}")
        return s
    if kind == "window":
        v = s.lower()
        if not _WINDOW_RE.match(v):
            raise ValueError(f"expected today|all|lifetime|<N>h|d|w|m|y, got {raw!r}")
        return v
    return s


def _toml_escape_key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", key):
        return key
    return json_quote(key)


def json_quote(s: str) -> str:
    import json

    return json.dumps(s, ensure_ascii=False)


def _format_toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return json_quote(value)
    raise TypeError(f"unsupported TOML value type: {type(value)!r}")


def dump_toml(data: dict[str, Any]) -> str:
    """Minimal TOML encoder for relay.toml-shaped nested dicts."""

    lines: list[str] = []

    def emit_table(path: str, table: dict[str, Any]) -> None:
        scalars: list[tuple[str, Any]] = []
        nested: list[tuple[str, dict[str, Any]]] = []
        for k, v in sorted(table.items()):
            if isinstance(v, dict):
                nested.append((k, v))
            else:
                scalars.append((k, v))
        if scalars:
            lines.append(f"[{path}]")
            for k, v in scalars:
                lines.append(f"{_toml_escape_key(k)} = {_format_toml_value(v)}")
            lines.append("")
        for k, sub in nested:
            emit_table(f"{path}.{k}", sub)

    for top_k, top_v in sorted(data.items()):
        if isinstance(top_v, dict):
            emit_table(top_k, top_v)
        else:
            lines.append(f"{_toml_escape_key(top_k)} = {_format_toml_value(top_v)}")
            lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def get_value(path: Path, key: str) -> tuple[bool, str]:
    cfg = _load_toml(path)
    if not is_key_allowed(key, cfg):
        return False, "Key not allowlisted (fail closed)."
    v = _get_nested(cfg, key)
    if v is None:
        return True, "(unset)"
    if isinstance(v, bool):
        return True, "true" if v else "false"
    return True, str(v)


def set_value(path: Path, key: str, raw: str) -> tuple[bool, str]:
    cfg = _load_toml(path)
    if not is_key_allowed(key, cfg):
        return False, "Key not allowlisted (fail closed)."
    kind = _key_kind(key, _effective_allow(cfg))
    if kind is None:
        return False, "Key not allowlisted (fail closed)."
    try:
        value = _coerce_value(kind, raw)
    except ValueError as exc:
        return False, str(exc)

    if path.is_file():
        bak = path.with_name(path.name + ".bak")
        shutil.copy2(path, bak)

    data = _load_toml(path) if path.is_file() else {}
    _set_nested(data, key, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_toml(data), encoding="utf-8")

    hint = _RESTART_HINTS.get(key, "")
    msg = f"OK — set {key} = {_format_toml_value(value)}"
    if hint:
        msg += f"\n{hint}"
    return True, msg


def format_summary(path: Path) -> str:
    cfg = _load_toml(path)
    lines = [
        "⚙️ Relay config (safe summary)",
        f"File: {path}",
        "",
        "Usage:",
        f"  enabled = {_fmt(_get_nested(cfg, 'usage.enabled'), 'false')}",
        f"  source = {_fmt(_get_nested(cfg, 'usage.source'), 'claude-code')}",
        f"  window = {_fmt(_get_nested(cfg, 'usage.window'), '7d')}",
        f"  providers = {_fmt(_get_nested(cfg, 'usage.providers'), 'true')}",
        f"  models = {_fmt(_get_nested(cfg, 'usage.models'), 'true')}",
        f"  charts.default = {_fmt(_get_nested(cfg, 'usage.charts.default'), 'bar')}",
        "",
        "Dashboard:",
        f"  window_hours = {_fmt(_get_nested(cfg, 'dashboard.window_hours'), '24')}",
        "",
        "General:",
        f"  page_size = {_fmt(_get_nested(cfg, 'general.page_size'), '3500')}",
        f"  send_interval_ms = {_fmt(_get_nested(cfg, 'general.send_interval_ms'), '350')}",
        "",
        "Format:",
        f"  enabled = {_fmt(_get_nested(cfg, 'format.enabled'), 'true')}",
        f"  wrap_width = {_fmt(_get_nested(cfg, 'format.wrap_width'), '50')}",
        "",
        "TTS:",
        f"  mode = {_fmt(_get_nested(cfg, 'tts.mode'), 'off')}",
        f"  hook_voice = {_fmt(_get_nested(cfg, 'tts.hook_voice'), 'true')}",
        "",
        "Routing:",
        f"  require_prefix = {_fmt(_get_nested(cfg, 'routing.require_prefix'), 'false')}",
        "",
        "Commands: /config help · get · set · charts · usage window · allot",
        "Secrets (BOT_TOKEN, allowlist) live in .env only — never shown here.",
    ]
    return "\n".join(lines)


def _fmt(v: Any, default: str) -> str:
    if v is None:
        return default
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def format_help(cfg: dict[str, Any] | None = None) -> str:
    allow = sorted(_effective_allow(cfg or {}))
    lines = [
        "Allowlisted keys (relay.toml):",
        "",
    ]
    for k in allow:
        kind = _BUILTIN_ALLOW.get(k, "str")
        lines.append(f"  {k}  ({kind})")
    lines.extend(
        [
            "  usage.allotments.<provider>.<period>  (number)",
            "",
            "Shortcuts:",
            "  /config charts <bar|line|both>",
            "  /config usage window <7d|30d|today|…>",
            "  /config allot <provider> <period> <number>",
            "",
            'Optional: [config.remote] allow = ["extra.dotted.key"] in relay.toml',
        ]
    )
    return "\n".join(lines)


def parse_config_text(text: str) -> tuple[str, list[str]]:
    """Return (action, args) from flattened command text."""
    t = text.strip()
    for prefix in ("/config", "config"):
        if t.lower().startswith(prefix):
            t = t[len(prefix) :].strip()
            break
    if not t:
        return "summary", []
    parts = t.split()
    action = parts[0].lower()
    return action, parts[1:]


def handle_text(path: Path, text: str) -> str:
    action, args = parse_config_text(text)
    cfg = _load_toml(path)

    if action in ("help", "?"):
        return format_help(cfg)
    if action == "get":
        if not args:
            return "Usage: /config get <dotted.key>"
        ok, msg = get_value(path, args[0])
        return msg if ok else f"❌ {msg}"
    if action == "set":
        if len(args) < 2:
            return "Usage: /config set <dotted.key> <value>"
        key, value = args[0], " ".join(args[1:])
        ok, msg = set_value(path, key, value)
        return f"✅ {msg}" if ok else f"❌ {msg}"
    if action == "charts":
        if not args:
            return "Usage: /config charts <bar|line|both>"
        ok, msg = set_value(path, "usage.charts.default", args[0])
        return f"✅ {msg}" if ok else f"❌ {msg}"
    if action == "usage" and len(args) >= 2 and args[0].lower() == "window":
        ok, msg = set_value(path, "usage.window", " ".join(args[1:]))
        return f"✅ {msg}" if ok else f"❌ {msg}"
    if action == "allot":
        if len(args) < 3:
            return "Usage: /config allot <provider> <period> <number>"
        provider, period, number = args[0], args[1], args[2]
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", provider):
            return "❌ Invalid provider name."
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", period):
            return "❌ Invalid period name."
        key = f"usage.allotments.{provider}.{period}"
        ok, msg = set_value(path, key, number)
        return f"✅ {msg}" if ok else f"❌ {msg}"
    return format_summary(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Safe relay.toml remote config")
    p.add_argument("--toml", type=Path, default=_repo_root() / "relay.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("handle", help="Parse Telegram command text")
    h.add_argument("text", nargs="?", default="")

    g = sub.add_parser("get")
    g.add_argument("key")

    s = sub.add_parser("set")
    s.add_argument("key")
    s.add_argument("value")

    sub.add_parser("summary")
    sub.add_parser("help")

    args = p.parse_args(argv)
    path: Path = args.toml

    if args.cmd == "handle":
        print(handle_text(path, args.text))
        return 0
    if args.cmd == "get":
        ok, msg = get_value(path, args.key)
        print(msg if ok else f"ERROR: {msg}", file=sys.stderr if not ok else sys.stdout)
        return 0 if ok else 1
    if args.cmd == "set":
        ok, msg = set_value(path, args.key, args.value)
        print(msg if ok else f"ERROR: {msg}", file=sys.stderr if not ok else sys.stdout)
        return 0 if ok else 1
    if args.cmd == "summary":
        print(format_summary(path))
        return 0
    if args.cmd == "help":
        print(format_help(_load_toml(path)))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Bot identity — per-bot tokens, state isolation, and backend binding.

Why this exists
---------------
The relay historically had exactly one bot: a single ``BOT_TOKEN``, one poll
loop, one ``.offset``. Giving Claude and Grok dedicated bots (so each has its
own relay channel to the operator) means running more than one poll loop, and
that is not safe today for a reason that is easy to miss:

**Telegram's ``getUpdates`` cursor is per bot.** Two poll loops sharing one
``<bridge>/.offset`` clobber each other's cursor — each advances past updates
the other never saw, and messages are silently eaten. The same applies to the
reassembly buffers: a user can add both bots to the *same* group, in which case
the chat id is identical and the per-chat buffer files would collide.

So per-bot state isolation is a prerequisite for multi-bot, not a detail.

How it works
------------
Each bot gets a state directory holding its own ``.offset`` and
``.tg-buffer*`` files. The **default bot keeps the bridge root itself**, so an
existing single-bot deployment reads and writes byte-for-byte the same paths as
before — this module is inert until a ``[bots.*]`` table is configured.

    <bridge>/.offset                  default bot (legacy layout, unchanged)
    <bridge>/.bots/grok/.offset       bot id "grok"

Because isolation is a directory rather than a filename suffix, every existing
glob (``.tg-buffer-ts.*``) keeps working verbatim against the new root.

Config
------
    [bots.claude]
    token_env = "BOT_TOKEN"          # optional; defaults to BOT_TOKEN

    [bots.grok]
    token_env = "BOT_TOKEN_GROK"

    [backends.grok]
    bot = "grok"                     # which bot serves this backend

Tokens are never stored in ``relay.toml`` — only the *name* of the environment
variable holding one. Values stay in ``.env`` / the secret store.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_TOKEN_ENV",
    "backends_for_bot",
    "bot_for_backend",
    "bot_ids",
    "bot_state_dir",
    "bot_token",
    "bots_configured",
    "bots_table",
    "has_multi_bot",
    "is_default_bot",
    "selected_bot",
    "token_env_for_bot",
]

DEFAULT_TOKEN_ENV = "BOT_TOKEN"
# Spellings that mean "the original single-bot identity", i.e. legacy paths.
_DEFAULT_ALIASES = ("", "default", "main")
_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"


def _safe_bot(bot: str) -> str:
    """Sanitise a bot id for use as a directory name.

    Bot ids come from ``relay.toml``, so never let one escape the state root
    via ``..`` or a path separator.
    """
    cleaned = "".join(c if c in _SAFE else "_" for c in str(bot))
    cleaned = cleaned.strip(".") or "_"
    return cleaned


def is_default_bot(bot: str | None) -> bool:
    """True when *bot* refers to the original single-bot identity."""
    return str(bot or "").strip().lower() in _DEFAULT_ALIASES


def bots_table(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The ``[bots.*]`` table, or empty when unconfigured."""
    bots = cfg.get("bots")
    if not isinstance(bots, dict):
        return {}
    return {str(k): v for k, v in bots.items() if isinstance(v, dict)}


def bot_ids(cfg: dict[str, Any]) -> list[str]:
    """Configured bot ids, sorted. Empty when running single-bot."""
    return sorted(bots_table(cfg))


def bots_configured(cfg: dict[str, Any]) -> bool:
    """True when any ``[bots.*]`` table exists (multi-bot features are active)."""
    return bool(bots_table(cfg))


def has_multi_bot(cfg: dict[str, Any]) -> bool:
    """True when more than one bot is configured."""
    return len(bots_table(cfg)) > 1


def bot_state_dir(bridge_dir: Path | str, bot: str = "") -> Path:
    """Directory holding *bot*'s offset and reassembly buffers.

    The default bot uses the bridge root unchanged, so existing deployments
    keep their exact ``.offset`` / ``.tg-buffer*`` paths.
    """
    root = Path(bridge_dir)
    if is_default_bot(bot):
        return root
    return root / ".bots" / _safe_bot(bot)


def token_env_for_bot(cfg: dict[str, Any], bot: str = "") -> str:
    """Name of the env var holding *bot*'s token (never the token itself)."""
    entry = bots_table(cfg).get(str(bot or ""))
    if isinstance(entry, dict):
        raw = entry.get("token_env")
        if raw:
            return str(raw)
    return DEFAULT_TOKEN_ENV


def bot_token(
    cfg: dict[str, Any],
    bot: str = "",
    env_map: dict[str, str] | None = None,
) -> str:
    """Resolve *bot*'s token: process env first, then the ``.env`` map.

    Falls back to ``BOT_TOKEN`` for the default bot so a single-bot deployment
    needs no config change at all.
    """
    env_map = env_map or {}
    name = token_env_for_bot(cfg, bot)
    val = os.environ.get(name) or env_map.get(name) or ""
    if not val and name != DEFAULT_TOKEN_ENV and is_default_bot(bot):
        val = os.environ.get(DEFAULT_TOKEN_ENV) or env_map.get(DEFAULT_TOKEN_ENV) or ""
    return val


def bot_for_backend(cfg: dict[str, Any], backend: str) -> str:
    """Which bot serves *backend*. Empty means the default bot.

    Reads ``backends.<id>.bot``, falling back to ``routing.default_bot``.
    """
    backends = cfg.get("backends")
    if isinstance(backends, dict):
        entry = backends.get(str(backend))
        if isinstance(entry, dict) and entry.get("bot"):
            return str(entry["bot"])
    routing = cfg.get("routing")
    if isinstance(routing, dict) and routing.get("default_bot"):
        return str(routing["default_bot"])
    return ""


def backends_for_bot(cfg: dict[str, Any], bot: str = "") -> list[str]:
    """Backend ids served by *bot*, sorted.

    Backends with no explicit binding belong to the default bot, so a config
    that never mentions ``bot =`` behaves exactly as it does single-bot.
    """
    backends = cfg.get("backends")
    if not isinstance(backends, dict):
        return []
    out: list[str] = []
    for bid, entry in backends.items():
        if not isinstance(entry, dict):
            continue
        owner = bot_for_backend(cfg, str(bid))
        if (is_default_bot(bot) and is_default_bot(owner)) or str(owner) == str(bot):
            out.append(str(bid))
    return sorted(out)


def selected_bot(cfg: dict[str, Any], explicit: str = "") -> str:
    """Which bot this process serves: explicit arg, then ``RELAY_BOT``, then default.

    An id that is not in ``[bots.*]`` is still honoured — it simply gets its own
    state directory and the default token env — so a bot can be run before its
    config lands without silently sharing the default bot's cursor.
    """
    if explicit:
        return explicit
    return os.environ.get("RELAY_BOT", "").strip()

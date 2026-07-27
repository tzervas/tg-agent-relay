#!/usr/bin/env python3
"""tests/test_bots.py — Offline unit tests for tg_agent_relay.bots.

Dedicated bots for Claude and Grok mean more than one poll loop. Telegram's
getUpdates cursor is per bot, so the property that actually matters here is
that two bots never share an offset file — if they did, each would advance past
updates the other never saw and messages would be silently eaten.

Covers:
  - default bot keeps the legacy layout exactly (back-compat)
  - named bots get isolated state dirs, and a bot id cannot escape the root
  - per-bot token env resolution, with BOT_TOKEN fallback
  - backend → bot binding and the reverse lookup
  - two bots' offsets are genuinely independent (the whole point)

NO network. Stdlib-only PASS/FAIL runner.
Run:  python3 tests/test_bots.py
      uv run python tests/test_bots.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tg_agent_relay.bots import (
    backends_for_bot,
    bot_for_backend,
    bot_ids,
    bot_state_dir,
    bot_token,
    bots_configured,
    has_multi_bot,
    is_default_bot,
    selected_bot,
    token_env_for_bot,
)
from tg_agent_relay.poll import read_offset, write_offset

PASS = FAIL = 0


def ok(name: str) -> None:
    global PASS
    PASS += 1
    print(f"PASS  {name}")


def fail(name: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"FAIL  {name}")
    if detail:
        print(f"      {detail}")


def eq(name: str, exp, act) -> None:
    if exp == act:
        ok(name)
    else:
        fail(name, f"expected {exp!r} got {act!r}")


def true(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail)


CFG = {
    "bots": {
        "claude": {"token_env": "BOT_TOKEN"},
        "grok": {"token_env": "BOT_TOKEN_GROK"},
    },
    "backends": {
        "fleet": {"delivery": "fifo", "bot": "grok"},
        "cabal": {"delivery": "fifo", "bot": "grok"},
        "claude": {"delivery": "fifo", "bot": "claude"},
        "legacy": {"delivery": "stdout"},
    },
}


def main() -> int:
    # --- default identity aliases ------------------------------------------
    for alias in ("", "default", "main", "DEFAULT"):
        true(f"{alias!r} is the default bot", is_default_bot(alias))
    true("named bot is not default", not is_default_bot("grok"))

    # --- back-compat: default bot keeps the legacy layout -------------------
    bridge = Path(tempfile.mkdtemp(prefix="tg-bots-test-"))
    eq("default bot state dir is the bridge root", bridge, bot_state_dir(bridge, ""))
    eq("'default' alias also maps to bridge root", bridge, bot_state_dir(bridge, "default"))
    eq("no bots configured → inert", False, bots_configured({}))
    eq("empty cfg has no bot ids", [], bot_ids({}))
    eq("unbound backend belongs to default bot", "", bot_for_backend({}, "anything"))

    # --- named bots are isolated -------------------------------------------
    grok_dir = bot_state_dir(bridge, "grok")
    eq("named bot gets its own dir", bridge / ".bots" / "grok", grok_dir)
    true(
        "named bot dir is under the bridge",
        str(grok_dir.resolve()).startswith(str(bridge.resolve())),
        str(grok_dir),
    )
    escaped = bot_state_dir(bridge, "../../etc")
    true(
        "traversal bot id cannot escape the state root",
        str(escaped.resolve()).startswith(str((bridge / ".bots").resolve())),
        str(escaped),
    )

    # --- THE property: two bots never share a cursor ------------------------
    d_default = bot_state_dir(bridge, "")
    d_grok = bot_state_dir(bridge, "grok")
    d_claude = bot_state_dir(bridge, "claude")
    for d in (d_grok, d_claude):
        d.mkdir(parents=True, exist_ok=True)

    write_offset(d_default, 100)
    write_offset(d_grok, 200)
    write_offset(d_claude, 300)

    eq("default offset independent", 100, read_offset(d_default))
    eq("grok offset independent", 200, read_offset(d_grok))
    eq("claude offset independent", 300, read_offset(d_claude))

    # Advancing one bot must not move another.
    write_offset(d_grok, 999)
    eq("advancing grok leaves default alone", 100, read_offset(d_default))
    eq("advancing grok leaves claude alone", 300, read_offset(d_claude))
    eq("grok advanced", 999, read_offset(d_grok))

    true(
        "default bot's offset is still the legacy path",
        (bridge / ".offset").is_file(),
        str(list(bridge.iterdir())),
    )

    # --- token env resolution ----------------------------------------------
    eq("configured token env", "BOT_TOKEN_GROK", token_env_for_bot(CFG, "grok"))
    eq("claude bot uses BOT_TOKEN", "BOT_TOKEN", token_env_for_bot(CFG, "claude"))
    eq("unknown bot falls back to BOT_TOKEN", "BOT_TOKEN", token_env_for_bot(CFG, "nope"))
    eq("no bots table → BOT_TOKEN", "BOT_TOKEN", token_env_for_bot({}, ""))

    env_map = {"BOT_TOKEN": "tok-default", "BOT_TOKEN_GROK": "tok-grok"}
    eq("grok token from its own env var", "tok-grok", bot_token(CFG, "grok", env_map))
    eq("claude token from BOT_TOKEN", "tok-default", bot_token(CFG, "claude", env_map))
    eq("missing token yields empty", "", bot_token(CFG, "grok", {"BOT_TOKEN": "x"}))
    # Single-bot deployments keep working with only BOT_TOKEN set.
    eq("default bot falls back to BOT_TOKEN", "tok-default", bot_token({}, "", env_map))

    # Process env wins over the .env map.
    os.environ["BOT_TOKEN_GROK"] = "from-process-env"
    try:
        eq("process env wins", "from-process-env", bot_token(CFG, "grok", env_map))
    finally:
        del os.environ["BOT_TOKEN_GROK"]

    # --- backend binding ----------------------------------------------------
    eq("backend bound to grok", "grok", bot_for_backend(CFG, "fleet"))
    eq("backend bound to claude", "claude", bot_for_backend(CFG, "claude"))
    eq("unbound backend → default", "", bot_for_backend(CFG, "legacy"))
    eq("grok serves its backends", ["cabal", "fleet"], backends_for_bot(CFG, "grok"))
    eq("claude serves its backend", ["claude"], backends_for_bot(CFG, "claude"))
    eq("default bot serves unbound backends", ["legacy"], backends_for_bot(CFG, ""))

    # routing.default_bot as the fallback owner
    cfg_dflt = {
        "bots": {"grok": {}},
        "routing": {"default_bot": "grok"},
        "backends": {"x": {"delivery": "fifo"}},
    }
    eq("routing.default_bot binds unbound backends", "grok", bot_for_backend(cfg_dflt, "x"))
    eq("and the reverse lookup agrees", ["x"], backends_for_bot(cfg_dflt, "grok"))

    # --- discovery ----------------------------------------------------------
    eq("bot ids sorted", ["claude", "grok"], bot_ids(CFG))
    eq("multi-bot detected", True, has_multi_bot(CFG))
    eq("single bot is not multi", False, has_multi_bot({"bots": {"only": {}}}))
    eq("bots configured", True, bots_configured(CFG))

    # --- selection ----------------------------------------------------------
    eq("explicit selection wins", "grok", selected_bot(CFG, "grok"))
    os.environ["RELAY_BOT"] = "claude"
    try:
        eq("RELAY_BOT selects the bot", "claude", selected_bot(CFG))
        eq("explicit still overrides RELAY_BOT", "grok", selected_bot(CFG, "grok"))
    finally:
        del os.environ["RELAY_BOT"]
    eq("no selection → default", "", selected_bot(CFG))

    # --- channels must not cross -------------------------------------------
    # A message arriving on the Grok bot must never be delivered into a backend
    # owned by the Claude bot, or the two relay channels merge.
    from tg_agent_relay.poll import deliver_to_backend

    cross = Path(tempfile.mkdtemp(prefix="tg-bots-cross-"))
    cfg_cross = {
        "bots": {"claude": {}, "grok": {}},
        "backends": {
            "claudeb": {"delivery": "stdout", "tag": "claudeb", "bot": "claude"},
            "grokb": {"delivery": "stdout", "tag": "grokb", "bot": "grok"},
        },
    }

    os.environ["RELAY_BOT"] = "grok"
    try:
        own = deliver_to_backend(cfg_cross, "grokb", "", "for grok", bridge_dir=cross)
        other = deliver_to_backend(cfg_cross, "claudeb", "", "for claude", bridge_dir=cross)
    finally:
        del os.environ["RELAY_BOT"]

    true("grok bot delivers its own backend", own and "for grok" in own[0], str(own))
    eq("grok bot does not deliver claude's backend", [], other)
    metrics = (cross / ".metrics.log").read_text(encoding="utf-8")
    true(
        "cross-bot delivery is recorded as filtered",
        "message_filtered" in metrics and "bot=claude" in metrics,
        metrics,
    )

    # With no [bots.*] table the filter must be completely inert.
    legacy = Path(tempfile.mkdtemp(prefix="tg-bots-legacy-"))
    cfg_legacy = {"backends": {"anyb": {"delivery": "stdout", "tag": "anyb"}}}
    os.environ["RELAY_BOT"] = "grok"
    try:
        out_legacy = deliver_to_backend(cfg_legacy, "anyb", "", "hello", bridge_dir=legacy)
    finally:
        del os.environ["RELAY_BOT"]
    true(
        "single-bot config ignores RELAY_BOT entirely",
        out_legacy and "hello" in out_legacy[0],
        str(out_legacy),
    )

    print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def test_bots() -> None:
    """pytest entry point (dual-run with the standalone script form)."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())

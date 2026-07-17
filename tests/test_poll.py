#!/usr/bin/env python3
"""tests/test_poll.py — Offline unit tests for tg_agent_relay.poll (issue #27).

Covers:
  - classify_command / command_field / dispatch_command (forward vs relay)
  - ALLOWED_USER_ID allowlist + chat_is_accepted
  - RS buffer reassembly + quiet-window flush
  - deliver_to_backend stdout (+ filter)
  - route via resolve
  - process_update / process_result offset advance
  - poll_loop / poll_once with mocked getUpdates (no network)

NO live Telegram. Stdlib-only PASS/FAIL runner.
Run:  python3 tests/test_poll.py
      uv run python tests/test_poll.py
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tg_agent_relay.poll import (
    JOINER,
    RS,
    append_message,
    buffer_paths,
    chat_is_accepted,
    classify_command,
    command_field,
    deliver_to_backend,
    dispatch_command,
    flush_buffer,
    flush_stale_buffers,
    join_buffer_parts,
    poll_loop,
    poll_once,
    process_result,
    process_update,
    read_offset,
    reassemble_window,
    resolve_command_match,
    write_offset,
)

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


def _tmp_bridge() -> Path:
    d = Path(tempfile.mkdtemp(prefix="tg-poll-test-"))
    return d


# --- reassemble_window ------------------------------------------------------
eq("reassemble_window default", 4, reassemble_window({}))
eq(
    "reassemble_window from cfg",
    9,
    reassemble_window({"general": {"reassemble_window": 9}}),
)
old_env = os.environ.get("TG_REASSEMBLE_WINDOW")
os.environ["TG_REASSEMBLE_WINDOW"] = "7"
eq("reassemble_window env override", 7, reassemble_window({"general": {"reassemble_window": 9}}))
if old_env is None:
    del os.environ["TG_REASSEMBLE_WINDOW"]
else:
    os.environ["TG_REASSEMBLE_WINDOW"] = old_env


# --- join_buffer_parts ------------------------------------------------------
eq("join empty", "", join_buffer_parts(""))
eq("join single", "hello", join_buffer_parts("hello" + RS))
eq(
    "join two with joiner",
    f"part1{JOINER}part2",
    join_buffer_parts("part1" + RS + "part2" + RS),
)
eq(
    "join flattens newlines",
    f"a b{JOINER}c",
    join_buffer_parts("a\nb" + RS + "c" + RS),
)


# --- buffer_paths -----------------------------------------------------------
bridge = _tmp_bridge()
b, t = buffer_paths(bridge, multi_chat=False)
eq("legacy buffer path", bridge / ".tg-buffer", b)
eq("legacy ts path", bridge / ".tg-buffer-ts", t)
b2, t2 = buffer_paths(bridge, "-1001", "42", multi_chat=True)
eq("multi-chat buffer", bridge / ".tg-buffer.-1001_t42", b2)
eq("multi-chat ts", bridge / ".tg-buffer-ts.-1001_t42", t2)


# --- classify_command (shell parity) ----------------------------------------
eq("no commands section -> empty", "", classify_command({}, "status update"))
eq("empty commands -> empty", "", classify_command({"commands": {}}, "/status"))

CMD_CFG = {
    "commands": {
        "status": {"keyword": "status", "slash": "/status", "tag": "status"},
        "pause": {"keyword": "pause", "slash": "/pause"},
        "helpme": {"keyword": "help", "tag": "assist"},
        "example": {
            "keyword": "example",
            "slash": "/example",
            "mode": "relay",
            "handler": "handlers/example-echo.sh",
        },
    }
}
eq("exact slash", "status", classify_command(CMD_CFG, "/status"))
eq("slash with args", "status", classify_command(CMD_CFG, "/status now please"))
eq("keyword with space", "status", classify_command(CMD_CFG, "status update please"))
eq("keyword with colon", "status", classify_command(CMD_CFG, "status: how's it going?"))
eq("second command", "pause", classify_command(CMD_CFG, "pause the deployment"))
eq("no match", "", classify_command(CMD_CFG, "just chatting here"))
eq("slash prefix boundary", "", classify_command(CMD_CFG, "/statusfoo"))
eq("keyword prefix boundary", "", classify_command(CMD_CFG, "statusfoo bar"))


# --- command_field / dispatch_command ---------------------------------------
eq("command_field default mode", "forward", command_field(CMD_CFG, "helpme", "mode", "forward"))
eq("command_field configured tag", "assist", command_field(CMD_CFG, "helpme", "tag", "helpme"))
eq(
    "dispatch forward uses tag",
    ["[telegram:cmd:assist] help me out"],
    dispatch_command(CMD_CFG, "helpme", "help me out", bridge_dir=bridge),
)

# relay handler: copy example-echo into bridge
handlers = bridge / "handlers"
handlers.mkdir(parents=True, exist_ok=True)
echo_src = REPO / "handlers" / "example-echo.sh"
if echo_src.is_file():
    echo_dst = handlers / "example-echo.sh"
    echo_dst.write_text(echo_src.read_text(encoding="utf-8"), encoding="utf-8")
    echo_dst.chmod(echo_dst.stat().st_mode | stat.S_IXUSR)
    # Make BRIDGE_DIR resolution work: example-echo uses dirname/.. from handlers/
    lines = dispatch_command(
        CMD_CFG,
        "example",
        "example payload text",
        bridge_dir=bridge,
        detach_handlers=False,
    )
    eq("relay dispatch emits nothing", [], lines)
    marker = bridge / ".example-echo-received"
    true("relay handler ran", marker.is_file() and marker.read_text() == "example payload text")
else:
    fail("relay handler fixture missing", str(echo_src))


# --- chat_is_accepted -------------------------------------------------------
eq(
    "accept allowed_chat_id",
    True,
    chat_is_accepted({}, "99", allowed_chat_id="99"),
)
eq(
    "reject other chat when allowed set",
    False,
    chat_is_accepted({}, "100", allowed_chat_id="99"),
)
eq(
    "accept any when no allowed and no routing",
    True,
    chat_is_accepted({}, "anything", allowed_chat_id=""),
)
cfg_chats = {
    "backends": {"claude": {"tag": "claude"}},
    "chats": [{"chat_id": -1001, "backend": "claude"}],
}
eq(
    "accept listed [[chats]] id",
    True,
    chat_is_accepted(cfg_chats, "-1001", allowed_chat_id=""),
)
eq(
    "reject unlisted when routing present",
    False,
    chat_is_accepted(cfg_chats, "-9999", allowed_chat_id="123"),
)
eq(
    "still accept ALLOWED_CHAT_ID with routing",
    True,
    chat_is_accepted(cfg_chats, "123", allowed_chat_id="123"),
)


# --- append + flush reassembly ----------------------------------------------
bridge2 = _tmp_bridge()
cfg_plain: dict = {}
append_message(bridge2, "hello", "1", "", multi_chat=False, now=1000)
append_message(bridge2, "world", "1", "", multi_chat=False, now=1001)
flushed = flush_buffer(bridge2, cfg_plain, multi_chat=False)
eq("flush joins with joiner", [f"[telegram] hello{JOINER}world"], flushed)
true("buffer cleared after flush", not (bridge2 / ".tg-buffer").read_text())


# --- flush_stale_buffers quiet window ---------------------------------------
bridge3 = _tmp_bridge()
append_message(bridge3, "stale msg", multi_chat=False, now=100)
# window=4, now=110 → stale
stale = flush_stale_buffers(bridge3, {}, window=4, now=110)
eq("stale buffer flushes", ["[telegram] stale msg"], stale)

bridge3b = _tmp_bridge()
append_message(bridge3b, "fresh", multi_chat=False, now=108)
fresh = flush_stale_buffers(bridge3b, {}, window=4, now=110)
eq("fresh buffer not flushed yet", [], fresh)


# --- deliver_to_backend stdout ----------------------------------------------
cfg_route = {
    "backends": {
        "claude": {"tag": "claude", "delivery": "stdout", "prefixes": ["@claude"]},
        "grok": {"tag": "grok", "delivery": "stdout", "prefixes": ["@grok"]},
    },
    "routing": {"default_backend": "claude"},
}
eq(
    "legacy no backend",
    ["[telegram] hi"],
    deliver_to_backend(cfg_route, "", "", "hi", bridge_dir=bridge2),
)
eq(
    "stdout tagged backend",
    ["[telegram:backend:claude] implement parser"],
    deliver_to_backend(cfg_route, "claude", "", "implement parser", bridge_dir=bridge2),
)
eq(
    "stdout backend+project tag",
    ["[telegram:backend:claude:project:mycelium] x"],
    deliver_to_backend(cfg_route, "claude", "mycelium", "x", bridge_dir=bridge2),
)
old_filter = os.environ.get("TG_POLL_BACKEND")
os.environ["TG_POLL_BACKEND"] = "grok"
eq(
    "TG_POLL_BACKEND filters other backends",
    [],
    deliver_to_backend(cfg_route, "claude", "", "nope", bridge_dir=bridge2),
)
if old_filter is None:
    del os.environ["TG_POLL_BACKEND"]
else:
    os.environ["TG_POLL_BACKEND"] = old_filter


# --- flush with routing.resolve ---------------------------------------------
bridge4 = _tmp_bridge()
append_message(bridge4, "@grok review this", "55", "", multi_chat=True, now=50)
lines = flush_buffer(bridge4, cfg_route, "55", "", multi_chat=True)
eq(
    "flush routes via resolve prefix",
    ["[telegram:backend:grok] review this"],
    lines,
)

# default backend unprefixed
bridge4b = _tmp_bridge()
append_message(bridge4b, "plain hello", "55", "", multi_chat=True, now=50)
lines_def = flush_buffer(bridge4b, cfg_route, "55", "", multi_chat=True)
eq(
    "flush uses default_backend",
    ["[telegram:backend:claude] plain hello"],
    lines_def,
)


# --- process_update allowlist + offset --------------------------------------
bridge5 = _tmp_bridge()
upd = {
    "update_id": 10,
    "message": {
        "from": {"id": 42},
        "chat": {"id": 99},
        "text": "secret",
    },
}
# wrong user → ignored, offset still advances
immed = process_update(
    upd,
    bridge_dir=bridge5,
    cfg={},
    allowed_user_id="999",
    allowed_chat_id="99",
    now=200,
)
eq("disallowed user emits nothing", [], immed)
eq("offset advanced for ignored", 11, read_offset(bridge5))
true(
    "no buffer for disallowed",
    not (bridge5 / ".tg-buffer").exists() or (bridge5 / ".tg-buffer").stat().st_size == 0,
)

# allowed user → buffered
immed2 = process_update(
    {
        "update_id": 11,
        "message": {"from": {"id": 999}, "chat": {"id": 99}, "text": "allowed text"},
    },
    bridge_dir=bridge5,
    cfg={},
    allowed_user_id="999",
    allowed_chat_id="99",
    now=201,
)
eq("allowed user no immediate emit (buffered)", [], immed2)
buf_raw = (bridge5 / ".tg-buffer").read_text(encoding="utf-8")
true("message buffered with RS", "allowed text" in buf_raw and RS in buf_raw)
eq("offset after allowed", 12, read_offset(bridge5))

# setup discovery when no ALLOWED_USER_ID
bridge5b = _tmp_bridge()
setup_lines = process_update(
    {
        "update_id": 1,
        "message": {"from": {"id": 777}, "chat": {"id": 1}, "text": "hi"},
    },
    bridge_dir=bridge5b,
    cfg={},
    allowed_user_id="",
    now=1,
)
eq(
    "setup discovery line",
    ["[telegram-setup] your user_id is 777"],
    setup_lines,
)

# non-text still advances offset
bridge5c = _tmp_bridge()
write_offset(bridge5c, 0)
process_update(
    {"update_id": 5, "message": {"from": {"id": 1}, "chat": {"id": 1}}},
    bridge_dir=bridge5c,
    cfg={},
    allowed_user_id="1",
)
eq("non-text advances offset", 6, read_offset(bridge5c))


# --- process_result multi-update --------------------------------------------
bridge6 = _tmp_bridge()
result = [
    {
        "update_id": 100,
        "message": {"from": {"id": 1}, "chat": {"id": 2}, "text": "a"},
    },
    {
        "update_id": 101,
        "message": {"from": {"id": 1}, "chat": {"id": 2}, "text": "b"},
    },
]
process_result(
    result,
    bridge_dir=bridge6,
    cfg={},
    allowed_user_id="1",
    allowed_chat_id="2",
    now=300,
)
raw = (bridge6 / ".tg-buffer").read_text(encoding="utf-8")
eq("two messages buffered", f"a{RS}b{RS}", raw)
eq("offset last+1", 102, read_offset(bridge6))


# --- poll_once / poll_loop mocked HTTP --------------------------------------
bridge7 = _tmp_bridge()
(bridge7 / ".env").write_text(
    "BOT_TOKEN=test-token-not-real\nALLOWED_USER_ID=42\nALLOWED_CHAT_ID=99\n",
    encoding="utf-8",
)
calls: list[tuple] = []


def fake_get_updates(token: str, offset: int, timeout: int, *, curl_maxtime: float = 60.0):
    calls.append((token, offset, timeout, curl_maxtime))
    return {
        "ok": True,
        "result": [
            {
                "update_id": 500,
                "message": {
                    "from": {"id": 42},
                    "chat": {"id": 99},
                    "text": "from mock",
                },
            }
        ],
    }


sleeps: list[float] = []
emitted: list[str] = []
status = poll_once(
    bridge_dir=bridge7,
    cfg={},
    get_updates_fn=fake_get_updates,
    now_fn=lambda: 1000,
    sleep_fn=lambda s: sleeps.append(s),
    emit=emitted.append,
    detach=False,
)
eq("poll_once status ok", "ok", status)
true("getUpdates called", len(calls) == 1 and calls[0][0] == "test-token-not-real")
true("message buffered by poll_once", "from mock" in (bridge7 / ".tg-buffer").read_text())
eq("offset after poll_once", 501, read_offset(bridge7))

# Second poll_once with empty result but stale buffer → flush
clock = {"t": 1000}


def now_adv():
    return clock["t"]


def fake_empty(token, offset, timeout, *, curl_maxtime=60.0):
    clock["t"] = 1010  # advance past reassemble window
    return {"ok": True, "result": []}


# Force buffer timestamp old enough
(bridge7 / ".tg-buffer-ts").write_text("1000", encoding="utf-8")
clock["t"] = 1010
status2 = poll_once(
    bridge_dir=bridge7,
    cfg={},
    get_updates_fn=fake_empty,
    now_fn=now_adv,
    sleep_fn=lambda s: None,
    emit=emitted.append,
    detach=False,
)
true("flush emitted on quiet window", any("from mock" in x for x in emitted))
eq(
    "flushed line format",
    True,
    any(x == "[telegram] from mock" for x in emitted),
)


# poll_loop max_iterations
bridge8 = _tmp_bridge()
(bridge8 / ".env").write_text(
    "BOT_TOKEN=t\nALLOWED_USER_ID=1\nALLOWED_CHAT_ID=1\n",
    encoding="utf-8",
)
n_calls = {"n": 0}


def gu_count(token, offset, timeout, *, curl_maxtime=60.0):
    n_calls["n"] += 1
    return {"ok": True, "result": []}


rc = poll_loop(
    bridge_dir=bridge8,
    max_iterations=3,
    get_updates_fn=gu_count,
    sleep_fn=lambda s: None,
    now_fn=lambda: 1,
    emit=lambda _l: None,
)
eq("poll_loop returns 0", 0, rc)
eq("poll_loop respects max_iterations", 3, n_calls["n"])


# poll_error path
bridge9 = _tmp_bridge()
(bridge9 / ".env").write_text("BOT_TOKEN=t\nALLOWED_USER_ID=1\n", encoding="utf-8")
st_err = poll_once(
    bridge_dir=bridge9,
    cfg={},
    get_updates_fn=lambda *a, **k: None,
    sleep_fn=lambda s: None,
    now_fn=lambda: 1,
)
eq("poll_error on None response", "poll_error", st_err)

st_bad = poll_once(
    bridge_dir=bridge9,
    cfg={},
    get_updates_fn=lambda *a, **k: {"ok": False},
    sleep_fn=lambda s: None,
    now_fn=lambda: 1,
)
eq("poll_error on api not ok", "poll_error", st_bad)


# chat rejected when not in acceptance list
bridge10 = _tmp_bridge()
cfg_strict = {
    "backends": {"c": {"tag": "c"}},
    "chats": [{"chat_id": -100, "backend": "c"}],
}
process_update(
    {
        "update_id": 9,
        "message": {"from": {"id": 1}, "chat": {"id": -999}, "text": "nope"},
    },
    bridge_dir=bridge10,
    cfg=cfg_strict,
    allowed_user_id="1",
    allowed_chat_id="-100",
    now=1,
)
true(
    "rejected chat not buffered",
    not (bridge10 / ".tg-buffer").exists()
    or ((bridge10 / ".tg-buffer").stat().st_size == 0 and not list(bridge10.glob(".tg-buffer.*"))),
)
eq("offset still advances on reject", 10, read_offset(bridge10))


# command path on flush
bridge11 = _tmp_bridge()
append_message(bridge11, "/status please", multi_chat=False, now=1)
cmd_lines = flush_buffer(bridge11, CMD_CFG, multi_chat=False, detach=False)
eq(
    "flush classifies command",
    ["[telegram:cmd:status] /status please"],
    cmd_lines,
)

cfg_cabal_config = {
    "backends": {
        "cabal": {"tag": "cabal", "delivery": "stdout", "prefixes": ["@cabal"]},
    },
    "routing": {"require_prefix": True},
    "commands": {"config": {"slash": "/config", "tag": "config"}},
}
eq(
    "resolve_command_match @cabal /config",
    ("config", "/config"),
    resolve_command_match(cfg_cabal_config, "@cabal /config"),
)
bridge_cabal_cmd = _tmp_bridge()
append_message(bridge_cabal_cmd, "@cabal /config", multi_chat=True, now=1)
cabal_cmd_lines = flush_buffer(bridge_cabal_cmd, cfg_cabal_config, multi_chat=True)
eq(
    "flush prefixed /config routes to command handler text",
    ["[telegram:cmd:config] /config"],
    cabal_cmd_lines,
)
eq("raw /config still classifies", "config", classify_command(cfg_cabal_config, "/config"))
bridge_cabal_raw = _tmp_bridge()
append_message(bridge_cabal_raw, "/config", multi_chat=True, now=1)
raw_cfg_lines = flush_buffer(bridge_cabal_raw, cfg_cabal_config, multi_chat=True)
eq(
    "flush raw /config routes to command",
    ["[telegram:cmd:config] /config"],
    raw_cfg_lines,
)
bridge_cabal_fifo = _tmp_bridge()
append_message(bridge_cabal_fifo, "@cabal hello", "77", "", multi_chat=True, now=1)
cabal_fifo_lines = flush_buffer(bridge_cabal_fifo, cfg_cabal_config, "77", "", multi_chat=True)
eq(
    "flush @cabal hello goes to backend fifo path not command",
    ["[telegram:backend:cabal] hello"],
    cabal_fifo_lines,
)

# inline callback: usage window
bridge_cb = _tmp_bridge()
cb_lines = process_update(
    {
        "update_id": 50,
        "callback_query": {
            "id": "cb1",
            "from": {"id": 1},
            "data": "usage:window:24h",
            "message": {"chat": {"id": -100}},
        },
    },
    bridge_dir=bridge_cb,
    cfg=CMD_CFG,
    allowed_user_id="1",
    allowed_chat_id="-100",
)
eq("usage callback emits cmd line", ["[telegram:cmd:usage] window=24h"], cb_lines)
eq("callback advances offset", 51, read_offset(bridge_cb))


# deliver cmd mode with RELAY_* env (sync)
bridge12 = _tmp_bridge()
marker = bridge12 / "cmd-ran.txt"
script = bridge12 / "run-cmd.sh"
script.write_text(
    '#!/bin/bash\nprintf \'%s|%s|%s|%s\' "$RELAY_TEXT" "$RELAY_BACKEND" '
    '"$RELAY_CHAT_ID" "$RELAY_THREAD_ID" > ' + json.dumps(str(marker))[1:-1] + "\n",
    encoding="utf-8",
)
# simpler portable script
script.write_text(
    "#!/bin/sh\n"
    f'printf "%s|%s|%s|%s" "$RELAY_TEXT" "$RELAY_BACKEND" '
    f'"$RELAY_CHAT_ID" "$RELAY_THREAD_ID" > "{marker}"\n',
    encoding="utf-8",
)
script.chmod(script.stat().st_mode | stat.S_IXUSR)
cfg_cmd = {
    "backends": {
        "ollama": {
            "delivery": "cmd",
            "cmd": [str(script)],
            "model": "llama",
        }
    }
}
out_cmd = deliver_to_backend(
    cfg_cmd,
    "ollama",
    "proj",
    "inject me",
    "chat1",
    "th2",
    bridge_dir=bridge12,
    detach_cmd=False,
)
eq("cmd delivery no stdout", [], out_cmd)
true("cmd ran with RELAY env", marker.is_file())
if marker.is_file():
    eq("RELAY_* values", "inject me|ollama|chat1|th2", marker.read_text())


# --- CLI main_poll import ---------------------------------------------------
from tg_agent_relay.cli import main_poll

true("main_poll callable", callable(main_poll))


# cleanup note: temp dirs left for OS tmp cleaner; no assert on cleanup

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

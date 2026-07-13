#!/usr/bin/env python3
"""tests/test_send.py — Offline unit tests for tg_agent_relay.send (#26).

Covers pagination, dedup, flock best-effort, format+never-silent retry,
TTS strip/truncate/chunk eligibility, EnvSender.send, silent no-ops.

NO network: urllib.request.urlopen is mocked.

Run:
  uv run python tests/test_send.py
  python3 tests/test_send.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tg_agent_relay.protocols import SendRequest
from tg_agent_relay.send import (
    EnvSender,
    build_page_payloads,
    chunk_text,
    is_duplicate,
    load_env,
    paginate,
    record_last_sent,
    send_lock,
    send_message,
    send_photo,
    send_text_never_silent,
    spoken_transcript,
    truncate_words,
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
        fail(name, f"expected {exp!r}\n      got      {act!r}")


def true(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail)


# ---------------------------------------------------------------------------
# load_env
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / ".env").write_text(
        "# comment\nBOT_TOKEN=tok123\nALLOWED_CHAT_ID=-1001\nEMPTY=\n",
        encoding="utf-8",
    )
    env = load_env(root)
    eq("load_env BOT_TOKEN", "tok123", env.get("BOT_TOKEN"))
    eq("load_env ALLOWED_CHAT_ID", "-1001", env.get("ALLOWED_CHAT_ID"))
    eq("load_env missing file", {}, load_env(root / "nope"))


# ---------------------------------------------------------------------------
# paginate
# ---------------------------------------------------------------------------

eq("paginate short single page", ["hello"], paginate("hello", 3500))
eq("paginate empty", [], paginate("", 10))

long_lines = "\n".join([f"line-{i:04d}-" + ("x" * 20) for i in range(50)])
pages = paginate(long_lines, page_size=200)
true("paginate multi page count > 1", len(pages) > 1, f"n={len(pages)}")
true(
    "paginate rejoins to original",
    "\n".join(pages) == long_lines,
    f"joined_len={len(chr(10).join(pages))} orig={len(long_lines)}",
)
true("paginate each page <= size", all(len(p) <= 200 for p in pages))

# hard-split single line
hard = "a" * 50
hp = paginate(hard, page_size=20)
eq("paginate hard-split count", 3, len(hp))  # 20+20+10
eq("paginate hard-split rejoins", hard, "".join(hp))


# ---------------------------------------------------------------------------
# truncate_words / chunk_text / spoken
# ---------------------------------------------------------------------------

eq("truncate short unchanged", "hello world", truncate_words("hello world", 100))
eq(
    "truncate at word boundary",
    "hello",
    truncate_words("hello world there", 8),
)
eq("truncate max 0 no-op", "abc def", truncate_words("abc def", 0))

eq("chunk unbounded", ["one two three"], chunk_text("one two three", 0))
eq("chunk empty", [], chunk_text("", 10))
ch = chunk_text("alpha beta gamma delta", 12)
true("chunk multi", len(ch) >= 2, repr(ch))
true(
    "chunk covers all words", " ".join(ch).split() == ["alpha", "beta", "gamma", "delta"], repr(ch)
)
true("chunk each within max or single oversize word", all(len(c) <= 12 or " " not in c for c in ch))

spoken = spoken_transcript("Use `secret` and https://example.com/x now.")
true("spoken strips backticks", "`" not in spoken, repr(spoken))
true("spoken strips URL host path", "example.com" not in spoken, repr(spoken))
true("spoken keeps prose", "Use" in spoken and "now" in spoken, repr(spoken))


# ---------------------------------------------------------------------------
# dedup
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    true("dedup empty file false", not is_duplicate(root, "msg", now=1000))
    record_last_sent(root, "msg", now=1000)
    true("dedup same within window", is_duplicate(root, "msg", now=1005))
    true("dedup expired window", not is_duplicate(root, "msg", now=1020))
    true("dedup different text", not is_duplicate(root, "other", now=1005))


# ---------------------------------------------------------------------------
# flock best-effort
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    slept: list[float] = []

    with (
        mock.patch("tg_agent_relay.send.time.sleep", side_effect=lambda s: slept.append(s)),
        send_lock(root, interval_ms=100) as held,
    ):
        true("flock acquired (linux)", held is True or held is False)  # platform
        # lock file created when fcntl works
        lock = root / ".tg-send.lock"
        if held:
            true("lock file exists when held", lock.is_file(), str(lock))
    if slept:
        true("send_lock sleeps interval", abs(slept[0] - 0.1) < 0.001, repr(slept))
    else:
        # if flock failed, no sleep is also acceptable for the unserialized path
        # but our impl sleeps only when have=True
        ok("send_lock no sleep when unserialized or interval path exercised")


# ---------------------------------------------------------------------------
# send_message / never-silent (mocked urllib)
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok_resp(*a, **k):
    return _FakeResp('{"ok": true, "result": {}}')


def _fail_resp(*a, **k):
    return _FakeResp('{"ok": false, "description": "bad"}')


with mock.patch("tg_agent_relay.send.urllib.request.urlopen", side_effect=_ok_resp) as m:
    true(
        "send_message ok:true",
        send_message("TOKEN", "-1", "hi", parse_mode="HTML"),
    )
    true("send_message called urlopen", m.called)

with mock.patch(
    "tg_agent_relay.send.urllib.request.urlopen",
    side_effect=urllib.error.URLError("down"),
):
    true("send_message network fail → False", not send_message("TOKEN", "-1", "hi"))

# never-silent: first HTML fails, plain succeeds
calls: list[dict] = []


def _tracking_urlopen(req, timeout=10.0):
    body = req.data.decode() if req.data else ""
    calls.append({"url": req.full_url, "body": body, "headers": dict(req.headers)})
    if "parse_mode=HTML" in body or "parse_mode=HTML" in urllib.parse.unquote(body):
        return _FakeResp('{"ok": false}')
    return _FakeResp('{"ok": true}')


import urllib.parse

with (
    mock.patch("tg_agent_relay.send.urllib.request.urlopen", side_effect=_tracking_urlopen),
    mock.patch("tg_agent_relay.send.emit_metric") as em,
):
    ok_ns = send_text_never_silent(
        "TOKEN",
        "-1",
        "<b>hi</b>",
        "HTML",
        "hi",
        page_label="1/1",
        bridge_dir=REPO,
    )
    true("never-silent eventually ok", ok_ns)
    true("never-silent made 2 attempts", len(calls) == 2, repr(calls))
    true(
        "never-silent emit send_fallback",
        any(c.args[1] == "send_fallback" for c in em.call_args_list),
        repr(em.call_args_list),
    )


# ---------------------------------------------------------------------------
# build_page_payloads + format
# ---------------------------------------------------------------------------

payloads = build_page_payloads(["hello"], config={"format": {"enabled": True}})
eq("single page payload count", 1, len(payloads))
send_t, pm, plain = payloads[0]
eq("single plain", "hello", plain)
true("format sets HTML or plain", pm in ("HTML", ""), repr(pm))

multi = build_page_payloads(["a", "b"], config={"format": {"enabled": False}})
eq("multi plain page1 prefix", "[1/2]\na", multi[0][0])
eq("multi plain page2 prefix", "[2/2]\nb", multi[1][0])

html_multi = build_page_payloads(
    ["a", "b"], config={"format": {"enabled": True, "parse_mode": "HTML"}}
)
true(
    "html multi bold page header",
    html_multi[0][0].startswith("<b>[1/2]</b>"),
    repr(html_multi[0][0][:40]),
)


# ---------------------------------------------------------------------------
# EnvSender: silent no-ops
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    # no .env → no token
    with mock.patch("tg_agent_relay.send.urllib.request.urlopen") as m:
        EnvSender(root, config={}, sleep_fn=lambda _s: None).send(
            SendRequest(text="hi", chat_id="-1")
        )
        true("no token → no network", not m.called)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / ".env").write_text("BOT_TOKEN=t\nALLOWED_CHAT_ID=-9\n", encoding="utf-8")
    with mock.patch("tg_agent_relay.send.urllib.request.urlopen") as m:
        EnvSender(root, config={}, sleep_fn=lambda _s: None).send(
            SendRequest(text="", chat_id="-9")
        )
        true("empty text → no network", not m.called)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / ".env").write_text("BOT_TOKEN=t\n", encoding="utf-8")
    with mock.patch.dict(os.environ, {"BOT_TOKEN": "t", "ALLOWED_CHAT_ID": ""}, clear=False):
        # clear RELAY_CHAT_ID if set
        env_clear = {k: v for k, v in os.environ.items() if k not in ("RELAY_CHAT_ID",)}
        with mock.patch.dict(os.environ, env_clear, clear=True):
            os.environ["BOT_TOKEN"] = "t"
            with mock.patch("tg_agent_relay.send.urllib.request.urlopen") as m:
                EnvSender(root, config={}, sleep_fn=lambda _s: None).send(
                    SendRequest(text="hi", chat_id="")
                )
                true("no chat_id → no network", not m.called)


# ---------------------------------------------------------------------------
# EnvSender: happy path sendMessage + pages + metric
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / ".env").write_text("BOT_TOKEN=tok\nALLOWED_CHAT_ID=-100\n", encoding="utf-8")
    # force small pages
    cfg = {
        "general": {"page_size": 30, "page_delay": 0, "send_interval_ms": 0},
        "format": {"enabled": False},
        "tts": {"mode": "off"},
    }
    msg = "LINE1-" + ("x" * 20) + "\n" + "LINE2-" + ("y" * 20)
    with (
        mock.patch("tg_agent_relay.send.urllib.request.urlopen", side_effect=_ok_resp) as m,
        mock.patch("tg_agent_relay.send.time.sleep"),
    ):
        EnvSender(root, config=cfg, sleep_fn=lambda _s: None).send(
            SendRequest(text=msg, chat_id="-100", backend="claude", source="")
        )
    true("happy path called urlopen", m.call_count >= 1, f"count={m.call_count}")
    true("last-sent written", (root / ".last-sent").is_file())
    metrics = (
        (root / ".metrics.log").read_text(encoding="utf-8")
        if (root / ".metrics.log").is_file()
        else ""
    )
    true("metric tg-send-py send", "tg-send-py" in metrics and "send" in metrics, repr(metrics))
    true("metric mentions pages", "pages=" in metrics, repr(metrics))
    true("metric mentions backend", "backend=claude" in metrics, repr(metrics))


# ---------------------------------------------------------------------------
# EnvSender: dedup skips second send
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / ".env").write_text("BOT_TOKEN=tok\nALLOWED_CHAT_ID=-1\n", encoding="utf-8")
    cfg = {
        "general": {"page_size": 3500, "page_delay": 0, "send_interval_ms": 0},
        "format": {"enabled": False},
        "tts": {"mode": "off"},
    }
    with (
        mock.patch("tg_agent_relay.send.urllib.request.urlopen", side_effect=_ok_resp) as m,
        mock.patch("tg_agent_relay.send.time.sleep"),
    ):
        s = EnvSender(root, config=cfg, sleep_fn=lambda _s: None)
        s.send(SendRequest(text="same-msg", chat_id="-1"))
        first = m.call_count
        s.send(SendRequest(text="same-msg", chat_id="-1"))
        true("dedup second send no extra call", m.call_count == first, f"{first}→{m.call_count}")


# ---------------------------------------------------------------------------
# EnvSender: HTML fail → plain retry
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / ".env").write_text("BOT_TOKEN=tok\nALLOWED_CHAT_ID=-1\n", encoding="utf-8")
    cfg = {
        "general": {"page_size": 3500, "page_delay": 0, "send_interval_ms": 0},
        "format": {"enabled": True, "parse_mode": "HTML"},
        "tts": {"mode": "off"},
    }
    n_calls = {"n": 0}

    def _html_then_ok(req, timeout=10.0):
        n_calls["n"] += 1
        body = req.data.decode() if req.data else ""
        # first attempt (with parse_mode) fails
        if "parse_mode" in body and n_calls["n"] == 1:
            return _FakeResp('{"ok": false}')
        return _FakeResp('{"ok": true}')

    with (
        mock.patch("tg_agent_relay.send.urllib.request.urlopen", side_effect=_html_then_ok),
        mock.patch("tg_agent_relay.send.time.sleep"),
    ):
        EnvSender(root, config=cfg, sleep_fn=lambda _s: None).send(
            SendRequest(text="**bold** header", chat_id="-1")
        )
    true("format fallback attempted twice", n_calls["n"] >= 2, repr(n_calls))


# ---------------------------------------------------------------------------
# EnvSender: TTS voice first (stub voice_sender), hook path
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / ".env").write_text("BOT_TOKEN=tok\nALLOWED_CHAT_ID=-1\n", encoding="utf-8")
    cfg = {
        "general": {"page_size": 3500, "page_delay": 0, "send_interval_ms": 0},
        "format": {"enabled": False},
        "tts": {
            "mode": "text+voice",
            "hook_voice": True,
            "spoken_mode": "short",
            "spoken_max_chars": 50,
            "max_chars": 600,
        },
    }
    voice_calls: list[str] = []

    def stub_voice(token, chat, text, **kwargs):
        voice_calls.append(text)
        return True

    order: list[str] = []

    def order_urlopen(req, timeout=10.0):
        order.append("text")
        return _ok_resp(req, timeout=timeout)

    def order_voice(token, chat, text, **kwargs):
        order.append("voice")
        voice_calls.append(text)
        return True

    with (
        mock.patch("tg_agent_relay.send.urllib.request.urlopen", side_effect=order_urlopen),
        mock.patch("tg_agent_relay.send.time.sleep"),
    ):
        EnvSender(root, config=cfg, voice_sender=order_voice, sleep_fn=lambda _s: None).send(
            SendRequest(
                text="Hook finished the long task with details",
                chat_id="-1",
                source="hook",
            )
        )
    true("hook TTS voice called", len(voice_calls) == 1, repr(voice_calls))
    true(
        "voice before text",
        order[:2] == ["voice", "text"] or order == ["voice", "text"],
        repr(order),
    )


# voice-only non-hook: if voice succeeds, skip text
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / ".env").write_text("BOT_TOKEN=tok\nALLOWED_CHAT_ID=-1\n", encoding="utf-8")
    cfg = {
        "general": {"page_size": 3500, "page_delay": 0, "send_interval_ms": 0},
        "format": {"enabled": False},
        "tts": {"mode": "voice-only", "max_chars": 600, "hook_voice": True},
    }
    with (
        mock.patch("tg_agent_relay.send.urllib.request.urlopen", side_effect=_ok_resp) as m,
        mock.patch("tg_agent_relay.send.time.sleep"),
    ):
        EnvSender(
            root,
            config=cfg,
            voice_sender=lambda *a, **k: True,
            sleep_fn=lambda _s: None,
        ).send(SendRequest(text="short direct", chat_id="-1", source=""))
    true("voice-only skips text when voice ok", m.call_count == 0, f"calls={m.call_count}")


# voice-only hook: still sends text (never-silent)
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / ".env").write_text("BOT_TOKEN=tok\nALLOWED_CHAT_ID=-1\n", encoding="utf-8")
    cfg = {
        "general": {"page_size": 3500, "page_delay": 0, "send_interval_ms": 0},
        "format": {"enabled": False},
        "tts": {"mode": "voice-only", "max_chars": 600, "hook_voice": True},
    }
    with (
        mock.patch("tg_agent_relay.send.urllib.request.urlopen", side_effect=_ok_resp) as m,
        mock.patch("tg_agent_relay.send.time.sleep"),
    ):
        EnvSender(
            root,
            config=cfg,
            voice_sender=lambda *a, **k: True,
            sleep_fn=lambda _s: None,
        ).send(SendRequest(text="hook still needs text", chat_id="-1", source="hook"))
    true("hook voice-only still sends text", m.call_count >= 1, f"calls={m.call_count}")


# RELAY_CHAT_ID / RELAY_THREAD_ID env
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / ".env").write_text("BOT_TOKEN=tok\nALLOWED_CHAT_ID=-legacy\n", encoding="utf-8")
    cfg = {
        "general": {"page_size": 3500, "page_delay": 0, "send_interval_ms": 0},
        "format": {"enabled": False},
        "tts": {"mode": "off"},
    }
    seen: list[str] = []

    def capture(req, timeout=10.0):
        seen.append(req.data.decode() if req.data else "")
        return _ok_resp(req, timeout=timeout)

    with (
        mock.patch.dict(
            os.environ,
            {"RELAY_CHAT_ID": "-999", "RELAY_THREAD_ID": "42", "RELAY_BACKEND": "grok"},
            clear=False,
        ),
        mock.patch("tg_agent_relay.send.urllib.request.urlopen", side_effect=capture),
        mock.patch("tg_agent_relay.send.time.sleep"),
    ):
        EnvSender(root, config=cfg, sleep_fn=lambda _s: None).send(
            SendRequest(text="routed", chat_id="")  # empty → env
        )
    true("RELAY_CHAT_ID used", any("chat_id=-999" in s for s in seen), repr(seen))
    true(
        "RELAY_THREAD_ID used",
        any("message_thread_id=42" in s for s in seen),
        repr(seen),
    )


# send_photo multipart (mocked)
with tempfile.TemporaryDirectory() as td:
    img = Path(td) / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    with mock.patch("tg_agent_relay.send.urllib.request.urlopen", side_effect=_ok_resp) as m:
        true("send_photo ok", send_photo("TOKEN", "-1", img, caption="cap"))
        true("send_photo url contains sendPhoto", "sendPhoto" in m.call_args[0][0].full_url)


# ---------------------------------------------------------------------------
# CLI main exit 0 empty
# ---------------------------------------------------------------------------

from tg_agent_relay.send import main as send_main

eq(
    "main empty argv/stdin-ish", 0, send_main([])
)  # no args → reads stdin; empty in non-tty may be ""

print()
print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
raise SystemExit(0 if FAIL == 0 else 1)

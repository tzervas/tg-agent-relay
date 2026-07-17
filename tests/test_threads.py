"""Unit tests for forum thread titles, resolve, and mocked createForumTopic."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tg_agent_relay import threads


def test_session_short_and_title():
    assert threads.session_short("019f6d8a-long-session-id") == "019f"
    assert threads.repo_short("owner/tg-agent-relay") == "tg-agent-relay"
    assert (
        threads.build_topic_title("019f6d8a-aaaa", "tg-agent-relay", "p13")
        == "019f · tg-agent-relay · p13"
    )
    assert threads.build_topic_title("019f6d8a-aaaa", "tg-agent-relay") == "019f · tg-agent-relay"
    assert threads.build_topic_title("short") == "short"
    parsed = threads.parse_topic_title("7a3f · emb-core · smoke")
    assert parsed["session_short"] == "7a3f"
    assert parsed["workstream"] == "smoke"


def test_find_binding_tiers():
    cfg = {
        "chats": [
            {"chat_id": -100, "thread_id": 1, "session": "s1", "project": "p1", "workstream": "w1"},
            {"chat_id": -100, "thread_id": 2, "session": "s1", "project": "p1"},
            {"chat_id": -100, "thread_id": 3, "session": "s1"},
        ]
    }
    hit = threads.find_binding(cfg, session="s1", project="p1", workstream="w1")
    assert hit and hit["thread_id"] == 1
    hit2 = threads.find_binding(cfg, session="s1", project="p1")
    assert hit2 and hit2["thread_id"] == 2
    hit3 = threads.find_binding(cfg, session="s1")
    assert hit3 and hit3["thread_id"] == 3


def test_resolve_outbound_order():
    cfg = {
        "chats": [
            {"chat_id": -200, "thread_id": 9, "session": "sess", "project": "repo"},
        ],
        "threads": {"enabled": True, "platform_chats": {"grok": -300}},
    }
    explicit = threads.resolve_outbound(cfg, chat_id="-1", thread_id="2", session="sess")
    assert explicit.chat_id == "-1" and explicit.thread_id == "2"

    binding = threads.resolve_outbound(cfg, session="sess", project="repo")
    assert binding.chat_id == "-200" and binding.thread_id == "9"

    plat = threads.resolve_outbound(cfg, platform="grok")
    assert plat.chat_id == "-300" and plat.thread_id == ""

    fb = threads.resolve_outbound(cfg, allowed_chat_id="-999", session="unknown")
    assert fb.chat_id == "-999"
    none = threads.resolve_outbound(cfg, allowed_chat_id="-999")
    assert none.match_kind == "none"


def test_allowed_create_and_rate_limit():
    cfg = {"threads": {"enabled": True, "platform_chats": {"grok": "-1001"}}}
    assert threads.chat_allowed_for_topic_create(cfg, "-1001")
    assert not threads.chat_allowed_for_topic_create(cfg, "-999")
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "t.json"
        lim = threads.CreateRateLimiter(2, state)
        assert lim.allow()
        lim.record(1000.0)
        lim.record(1100.0)
        assert not lim.allow(1200.0)


def test_overlay_upsert():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "bindings.json"
        row = threads.binding_row(chat_id=-100, thread_id=5, session="s", project="p")
        threads.upsert_overlay_binding(path, row)
        doc = json.loads(path.read_text())
        assert len(doc["chats"]) == 1
        row2 = threads.binding_row(chat_id=-100, thread_id=5, session="s2", project="p2")
        threads.upsert_overlay_binding(path, row2)
        doc2 = json.loads(path.read_text())
        assert len(doc2["chats"]) == 1
        assert doc2["chats"][0]["session"] == "s2"


def test_create_forum_topic_mocked():
    def fake_urlopen(req: object, timeout: float) -> bytes:
        return json.dumps({"ok": True, "result": {"message_thread_id": 42}}).encode()

    out = threads.create_forum_topic("tok", -100, "title", urlopen=fake_urlopen)
    assert out["message_thread_id"] == 42

    def bad(_req: object, _timeout: float) -> bytes:
        return json.dumps({"ok": False, "description": "nope"}).encode()

    with pytest.raises(RuntimeError, match="nope"):
        threads.create_forum_topic("tok", -100, "x", urlopen=bad)


def test_ensure_topic_uses_existing():
    cfg = {
        "threads": {"enabled": True, "platform_chats": {"grok": "-100"}},
        "chats": [{"chat_id": -100, "thread_id": 7, "session": "s", "project": "p"}],
    }
    with tempfile.TemporaryDirectory() as td:
        overlay = Path(td) / "bindings.json"
        _cid, tid, title = threads.ensure_topic(
            cfg,
            token="t",
            chat_id="-100",
            session="s",
            project="p",
            overlay_path=overlay,
            urlopen=lambda *_: b"",
        )
        assert tid == "7"
        assert title == threads.build_topic_title("s", "p")


def test_ensure_topic_creates_and_binds():
    cfg = {
        "threads": {"enabled": True, "platform_chats": {"grok": "-100"}, "max_creates_per_hour": 10}
    }

    def ok(_req: object, _timeout: float) -> bytes:
        return json.dumps({"ok": True, "result": {"message_thread_id": 99}}).encode()

    with tempfile.TemporaryDirectory() as td:
        overlay = Path(td) / "bindings.json"
        cid, tid, _title = threads.ensure_topic(
            cfg,
            token="t",
            chat_id="-100",
            session="newsess",
            project="proj",
            workstream="ws",
            overlay_path=overlay,
            urlopen=ok,
        )
        assert cid == "-100" and tid == "99"
        assert overlay.is_file()
        doc = json.loads(overlay.read_text())
        assert doc["chats"][0]["session"] == "newsess"


def test_resolve_from_environ():
    with tempfile.TemporaryDirectory() as td:
        bridge = Path(td)
        (bridge / "relay.toml").write_text(
            """
[threads]
enabled = true

[[chats]]
chat_id = -55
thread_id = 3
session = "envsess"
""",
            encoding="utf-8",
        )
        env = {"RELAY_SESSION": "envsess", "ALLOWED_CHAT_ID": "-1"}
        target = threads.resolve_from_environ(bridge, env=env)
        assert target.chat_id == "-55"
        assert target.thread_id == "3"


def main() -> int:
    test_session_short_and_title()
    test_find_binding_tiers()
    test_resolve_outbound_order()
    test_allowed_create_and_rate_limit()
    test_overlay_upsert()
    test_create_forum_topic_mocked()
    test_ensure_topic_uses_existing()
    test_ensure_topic_creates_and_binds()
    test_resolve_from_environ()
    print("PASS  test_threads (standalone)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

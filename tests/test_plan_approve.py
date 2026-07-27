"""Offline tests for plan approve storage + parsing."""

from __future__ import annotations

from pathlib import Path

from tg_agent_relay.plan_approve import (
    agent_emit_line,
    maybe_reply_markup_for_body,
    parse_callback_data,
    parse_text_reply,
    set_plan_status,
    store_pending_plan,
)


def test_store_and_text_approve(tmp_path: Path) -> None:
    body = "📋 PLAN\n\nWAVE_PLAN step 1"
    pid = store_pending_plan(tmp_path, body)
    assert pid
    hit = parse_text_reply("lgtm", bridge_dir=tmp_path)
    assert hit == ("approve", pid)
    assert set_plan_status(tmp_path, pid, "approved")
    assert agent_emit_line("approved", pid) == f"[telegram:plan] status=approved id={pid}"


def test_callback_parse() -> None:
    assert parse_callback_data("plan:approve:abc-1") == ("approve", "abc-1")
    assert parse_callback_data("plan:later:abc-1") == ("later", "abc-1")


def test_plan_keyboard_only_for_plan(tmp_path: Path) -> None:
    assert maybe_reply_markup_for_body("hello", tmp_path) is None
    kb = maybe_reply_markup_for_body("WAVE_PLAN do things", tmp_path)
    assert kb and "plan:approve:" in kb

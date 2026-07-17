"""Format tests for PR/Plan/Stop templates."""

from __future__ import annotations

from tg_agent_relay.comms_format import MessageKind, classify_message, format_outbound


def test_classify_pr_and_plan() -> None:
    assert classify_message("Opened PR https://github.com/o/r/pull/9") == MessageKind.PR
    assert classify_message("WAVE_PLAN step 1") == MessageKind.PLAN
    assert classify_message("x", hook_event="Stop") == MessageKind.STOP


def test_format_stop_adds_header_and_stamp(monkeypatch) -> None:
    monkeypatch.setenv("RELAY_REPO", "tzervas/foo")
    monkeypatch.setenv("RELAY_BRANCH", "feat/bar")
    out = format_outbound("🏁 done", hook_event="Stop", skip_stamp=False)
    assert out.startswith("🛑 STOP")
    assert "🏁 done" in out
    assert "repo=tzervas/foo" in out


def test_format_pr_header() -> None:
    out = format_outbound("review summary for https://github.com/a/b/pull/3", skip_stamp=True)
    assert out.startswith("📣 PR")


def test_idempotent_when_already_stamped() -> None:
    raw = "🏷 repo=x branch=y\nbody"
    assert format_outbound(raw) == raw

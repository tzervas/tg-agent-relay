"""Unit tests for goal-mode hook noise filtering."""

from __future__ import annotations

from tg_agent_relay.goal_events import (
    apply_goal_noise_policy,
    classify_goal_noise,
    filter_hook_summary,
    is_goal_noise_text,
)


def test_detect_goal_noise() -> None:
    assert is_goal_noise_text("update_goal failed: Goal is not Active")
    assert is_goal_noise_text("cannot mark complete without active goal")
    assert not is_goal_noise_text("PR merged")


def test_post_tool_failure_skips() -> None:
    assert (
        classify_goal_noise(
            "Goal is not Active",
            tool_name="update_goal",
            hook_event="PostToolUseFailure",
        )
        == "skip"
    )
    assert filter_hook_summary("⚠️ update_goal failed: Goal is not Active", hook_event="PostToolUseFailure") is None


def test_apply_soft_on_generic() -> None:
    out = apply_goal_noise_policy("update_goal failed", hook_event="")
    assert out is not None
    assert "Goal idle" in out
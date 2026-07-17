"""Unit tests for agent stamp construction (mock env/git)."""

from __future__ import annotations

from pathlib import Path

from tg_agent_relay.agent_stamp import (
    StampInfo,
    _parse_github_remote,
    build_stamp,
    build_stamp_info,
)


def test_parse_github_remotes() -> None:
    assert _parse_github_remote("git@github.com:tzervas/foo.git") == ("tzervas", "foo")
    assert _parse_github_remote("https://github.com/tzervas/bar") == ("tzervas", "bar")


def test_stamp_from_env() -> None:
    env = {
        "RELAY_REPO": "tzervas/demo",
        "RELAY_BRANCH": "feat/x",
        "RELAY_PR_NUMBER": "12",
        "RELAY_PR_STATE": "open",
    }
    info = build_stamp_info(env=env, cwd="/nonexistent")
    assert info.repo == "tzervas/demo"
    assert info.branch == "feat/x"
    assert info.pr_url == "https://github.com/tzervas/demo/pull/12"
    assert info.pr_state == "open"
    text = info.text()
    assert "🏷 repo=tzervas/demo branch=feat/x" in text
    assert "🔗 pr: https://github.com/tzervas/demo/pull/12" in text
    assert "📌 status=open" in text


def test_merged_hint_forces_status(tmp_path: Path) -> None:
    env = {
        "RELAY_REPO": "o/r",
        "RELAY_BRANCH": "main",
        "RELAY_PR_URL": "https://github.com/o/r/pull/1",
    }
    out = build_stamp(env=env, cwd=tmp_path, body_for_merge_hint="PR merged into main")
    assert "status=merged" in out


def test_stamp_info_lines_empty_pr_state() -> None:
    info = StampInfo("a/b", "main", "https://github.com/a/b/tree/main", "", "")
    assert "📌" not in info.text()

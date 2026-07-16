"""Unit tests for lib/remote_config.py (offline, temp relay.toml)."""

from __future__ import annotations

from pathlib import Path

import pytest

from remote_config import (
    get_value,
    handle_text,
    is_key_allowed,
    set_value,
)


@pytest.fixture
def toml_path(tmp_path: Path) -> Path:
    return tmp_path / "relay.toml"


def test_reject_secret_key(toml_path: Path) -> None:
    assert not is_key_allowed("BOT_TOKEN")
    assert not is_key_allowed("general.allowed_user_id")
    ok, msg = set_value(toml_path, "usage.bot_token", "x")
    assert not ok
    assert "allowlisted" in msg.lower()


def test_reject_unknown_key(toml_path: Path) -> None:
    ok, msg = set_value(toml_path, "backends.grok.fifo", "/tmp/evil")
    assert not ok
    assert "allowlisted" in msg.lower()


def test_set_get_roundtrip(toml_path: Path) -> None:
    ok, _ = set_value(toml_path, "usage.enabled", "true")
    assert ok
    ok, val = get_value(toml_path, "usage.enabled")
    assert ok and val == "true"
    ok, _ = set_value(toml_path, "usage.window", "30d")
    assert ok
    assert (toml_path.parent / "relay.toml.bak").is_file()


def test_allotment_key(toml_path: Path) -> None:
    out = handle_text(toml_path, "/config allot claude monthly 5000000")
    assert "✅" in out
    ok, val = get_value(toml_path, "usage.allotments.claude.monthly")
    assert ok and val == "5000000"


def test_charts_shortcut(toml_path: Path) -> None:
    out = handle_text(toml_path, "/config charts line")
    assert "✅" in out
    ok, val = get_value(toml_path, "usage.charts.default")
    assert ok and val == "line"


def test_usage_window_shortcut(toml_path: Path) -> None:
    out = handle_text(toml_path, "/config usage window 30d")
    assert "✅" in out
    ok, val = get_value(toml_path, "usage.window")
    assert ok and val == "30d"


def test_invalid_chart_rejected(toml_path: Path) -> None:
    out = handle_text(toml_path, "/config charts pie")
    assert "❌" in out


def test_summary_no_secrets(toml_path: Path) -> None:
    toml_path.write_text(
        "[usage]\nenabled = true\n",
        encoding="utf-8",
    )
    summary = handle_text(toml_path, "/config")
    assert "BOT_TOKEN=" not in summary
    assert "ALLOWED_USER_ID=" not in summary
    assert "enabled = true" in summary


def main() -> None:
    raise SystemExit(pytest.main([__file__, "-q"]))


if __name__ == "__main__":
    main()

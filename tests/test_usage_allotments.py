"""Pytest-focused tests for usage allotment pure functions."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

import usage_ingest as u

NOW = 1_700_000_000


def test_parse_allotments_nested_and_dotted() -> None:
    raw = {
        "claude-code": {"weekly": 5_000_000, "daily": 0},
        "total.monthly": 20_000_000,
    }
    parsed = u.parse_allotments(raw)
    assert parsed["claude-code"]["weekly"] == 5_000_000
    assert parsed["claude-code"]["daily"] is None
    assert parsed["total"]["monthly"] == 20_000_000


def test_allotment_percent_saturates_at_100() -> None:
    rows = [
        u.UsageRow(NOW, "anthropic", "m", "p", 2000, 0, 0, 0, "claude-code"),
    ]
    snap = u.allotment_usage_snapshot(rows, {"claude-code": {"daily": 1000}}, now=NOW)
    assert snap["claude-code"]["daily"]["percent"] == 100.0


def test_collect_includes_periods_when_allotments_passed() -> None:
    fixture = REPO_ROOT / "tests" / "fixtures" / "usage-synthetic"
    allot = {"total": {"weekly": 10_000_000}}
    agg = u.collect("claude-code", str(fixture), "7d", now=NOW, allotments=allot)
    assert "periods" in agg
    assert "weekly" in agg["periods"]["total"]

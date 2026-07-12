#!/usr/bin/env python3
"""tests/test_metrics_agg.py - Offline unit tests for lib/metrics_agg.py
(the pure aggregation - see its module docstring) and a graceful
present-vs-fallback check of lib/dashboard_render.py's image path.

NO network calls. Deterministic: every aggregation assertion pins `now` to
a fixed epoch (see filter_window's `now` param) rather than relying on wall
clock, so the fixture log's synthetic timestamps always land in the same
window relative to it.

Run standalone: `python3 tests/test_metrics_agg.py`
Called by tests/run-tests.sh (matching that file's PASS/FAIL summary style,
one Python process for the whole file - the pytest-less pattern this repo
already uses: plain asserts, an explicit runner, exit 0 iff everything
passed).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

import metrics_agg as m  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "metrics-synthetic.log"

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def ok(name: str) -> None:
    global PASS
    PASS += 1
    print(f"PASS  {name}")


def fail(name: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    FAILURES.append(name)
    print(f"FAIL  {name}")
    if detail:
        print(f"      {detail}")


def assert_eq(name: str, expected, actual) -> None:
    if expected == actual:
        ok(name)
    else:
        fail(name, f"expected: {expected!r}  actual: {actual!r}")


# The fixed "now" the fixture was authored against - see the fixture file's
# own comments for the row-by-row math this test's expectations come from.
NOW = 1000002000
WINDOW_HOURS = 1  # window_start = NOW - 3600 = 999998400


def _load_fixture_agg():
    rows = m.parse_log(str(FIXTURE))
    filtered, w_start, w_end = m.filter_window(rows, WINDOW_HOURS, now=NOW)
    return m.aggregate(filtered, WINDOW_HOURS, w_start, w_end), rows, filtered


print("== lib/metrics_agg.py: parse_log ==")
_rows_all = m.parse_log(str(FIXTURE))
# 15 lines in the fixture, 1 malformed (no tabs) -> 14 parsed rows.
assert_eq("parse_log skips the malformed line", 14, len(_rows_all))

_missing = m.parse_log(str(REPO_ROOT / "does-not-exist.log"))
assert_eq("parse_log on a missing file returns an empty list, never raises", [], _missing)

print("== lib/metrics_agg.py: filter_window ==")
_filtered, w_start, w_end = m.filter_window(_rows_all, WINDOW_HOURS, now=NOW)
# 14 parsed rows, 1 is the far-future (9999999999) row outside the window.
assert_eq("filter_window excludes the out-of-window future row", 13, len(_filtered))
assert_eq("filter_window window_end is the pinned now", NOW, w_end)
assert_eq("filter_window window_start is now - window_hours*3600", NOW - 3600, w_start)

print("== lib/metrics_agg.py: aggregate (synthetic fixture, pinned now) ==")
agg, _, _ = _load_fixture_agg()

assert_eq("total_events (window-filtered)", 13, agg["total_events"])
assert_eq("messages_in = 2x message_flushed + 1x forwarded + 1x relay-handled", 4, agg["messages_in"])
assert_eq("messages_out = count of tg-send:send events", 3, agg["messages_out"])
assert_eq("pages_sent = sum of pages=N across sends (1+2+1)", 4, agg["pages_sent"])
assert_eq("commands_by_name", {"helpme": 1, "dashboard": 1}, agg["commands_by_name"])
assert_eq("commands_by_mode", {"forward": 1, "relay": 1}, agg["commands_by_mode"])
assert_eq("hooks (enabled fires only)", {"Notification": 1, "SubagentStop": 2}, agg["hooks"])
assert_eq("hooks_disabled (suffix stripped)", {"PreToolUse": 1}, agg["hooks_disabled"])
assert_eq("poll_errors", 1, agg["poll_errors"])
assert_eq("generic_sends", 1, agg["generic_sends"])
assert_eq("hook_fires = sum(hooks.values())", 3, agg["hook_fires"])
assert_eq(
    "model_turns_avoided = hook_fires + relay-handled commands (3 + 1)",
    4,
    agg["model_turns_avoided"],
)

print("== lib/metrics_agg.py: aggregate on an empty window (no data yet) ==")
empty_agg = m.aggregate([], 24, NOW - 86400, NOW)
assert_eq("empty window: total_events", 0, empty_agg["total_events"])
assert_eq("empty window: model_turns_avoided", 0, empty_agg["model_turns_avoided"])
assert_eq("empty window: hooks", {}, empty_agg["hooks"])

print("== lib/metrics_agg.py: text renderers never raise + say the honest thing ==")
stats_text = m.render_text_stats(agg)
if "model-turns avoided: 4" in stats_text and "Declared estimate" in stats_text:
    ok("render_text_stats includes the honest model-turns-avoided line + its caveat")
else:
    fail("render_text_stats includes the honest model-turns-avoided line + its caveat", stats_text)

dash_text = m.render_text_dashboard(agg)
if "SubagentStop" in dash_text and "dashboard" in dash_text and "helpme" in dash_text:
    ok("render_text_dashboard includes hook + command breakdowns")
else:
    fail("render_text_dashboard includes hook + command breakdowns", dash_text)

empty_dash_text = m.render_text_dashboard(empty_agg)
if "no metrics recorded yet" in empty_dash_text:
    ok("render_text_dashboard is honest about an empty window (never fabricates data)")
else:
    fail("render_text_dashboard is honest about an empty window", empty_dash_text)

print("== lib/metrics_agg.py: CLI (stats + dashboard modes) ==")
import subprocess  # noqa: E402

cli_stats = subprocess.run(
    [sys.executable, str(REPO_ROOT / "lib" / "metrics_agg.py"), str(FIXTURE), "9999999", "stats"],
    capture_output=True,
    text=True,
    timeout=10,
)
# A huge window_hours pulls in every fixture row relative to the REAL wall
# clock too (fixture timestamps are all far in the past except the
# deliberate 9999999999 outlier, which a large-enough window also covers) -
# this just proves the CLI runs end-to-end and exits 0, not exact numbers.
assert_eq("CLI stats mode exits 0", 0, cli_stats.returncode)
if "Relay stats" in cli_stats.stdout:
    ok("CLI stats mode prints the stats header")
else:
    fail("CLI stats mode prints the stats header", cli_stats.stdout)

print("== lib/dashboard_render.py: image path when matplotlib IS available, else graceful TEXT fallback ==")
try:
    import matplotlib  # noqa: F401

    HAS_MPL = True
except ImportError:
    HAS_MPL = False

sys.path.insert(0, str(REPO_ROOT / "lib"))
import dashboard_render  # noqa: E402

with tempfile.TemporaryDirectory() as tmpdir:
    out_png = str(Path(tmpdir) / "dashboard.png")
    rc = dashboard_render.main([
        "dashboard_render.py",
        str(FIXTURE),
        str(WINDOW_HOURS),
        out_png,
    ])
    assert_eq("dashboard_render.main() never exits non-zero for a data/render condition", 0, rc)

if HAS_MPL:
    print("      (matplotlib present in this interpreter - exercising the IMAGE path)")
    with tempfile.TemporaryDirectory() as tmpdir:
        out_png = str(Path(tmpdir) / "dashboard.png")
        _agg, _, _ = _load_fixture_agg()
        rendered = dashboard_render._render_image(_agg, out_png)
        if rendered and Path(out_png).is_file() and Path(out_png).stat().st_size > 0:
            ok("matplotlib present: _render_image writes a non-empty PNG")
        else:
            fail("matplotlib present: _render_image writes a non-empty PNG")
else:
    print("SKIP  matplotlib not installed in this interpreter - IMAGE path not exercised here")
    print("      (never-silent: this line IS the record; lib/dashboard_render.py still")
    print("       degrades to the TEXT path above, which IS covered)")

# ============================================================================
print()
print("=" * 60)
print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
if FAIL > 0:
    print(f"FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
sys.exit(0)

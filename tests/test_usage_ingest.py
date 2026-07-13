#!/usr/bin/env python3
"""tests/test_usage_ingest.py - Offline unit tests for lib/usage_ingest.py
(the opt-in token-usage aggregation - see its module docstring) and a
graceful present-vs-fallback check of lib/dashboard_render.py's usage
panels/image path.

NO network calls, NO real usage data. Every test reads ONLY the synthetic,
fabricated fixture tree at tests/fixtures/usage-synthetic/ (fake project
names, fake session ids, fake token counts - see that directory's own
fixture files) - never a real ~/.claude/projects directory. Deterministic:
every aggregation assertion pins `now` to the fixed epoch the fixtures were
authored against (see NOW below and each fixture file's timestamps).

Run standalone: `python3 tests/test_usage_ingest.py`
Called by tests/run-tests.sh (same PASS/FAIL summary style as
test_metrics_agg.py - the pytest-less pattern this repo already uses).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

import usage_ingest as u  # noqa: E402

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "usage-synthetic"

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


# The fixed "now" the fixture tree was authored against - see
# tests/fixtures/usage-synthetic/*/*.jsonl's own timestamps for the
# row-by-row math this test's expectations come from (each within 1h-30h
# of NOW, except one deliberate 200h-old outlier used to prove window
# filtering).
NOW = 1700000000
WINDOW = "7d"  # window_start = NOW - 7*86400 = 1699395200


print("== lib/usage_ingest.py: infer_provider() ==")
assert_eq("claude-* -> anthropic", "anthropic", u.infer_provider("claude-opus-4-8"))
assert_eq("gpt-* -> openai", "openai", u.infer_provider("gpt-5"))
assert_eq("gemini-* -> google", "google", u.infer_provider("gemini-3-pro"))
assert_eq("grok-* -> xai", "xai", u.infer_provider("grok-4.5"))
assert_eq("unrecognized -> other", "other", u.infer_provider("mystery-model-x"))
assert_eq("empty/None -> other, never raises", "other", u.infer_provider(""))
assert_eq("case-insensitive", "anthropic", u.infer_provider("Claude-Opus-4-8"))
assert_eq("display_model opus", "Opus 4.8", u.display_model("claude-opus-4-8"))
assert_eq("display_model haiku", "Haiku 4.5", u.display_model("claude-haiku-4-5-20251001"))
assert_eq("display_model sonnet", "Sonnet 5", u.display_model("claude-sonnet-5"))

print("== lib/usage_ingest.py: resolve_window() ==")
ws, we, label = u.resolve_window("7d", now=NOW)
assert_eq("7d: window_start = now - 7*86400", NOW - 7 * 86400, ws)
assert_eq("7d: window_end = now", NOW, we)
assert_eq("7d: label", "7d", label)

ws, we, label = u.resolve_window("24h", now=NOW)
assert_eq("24h: window_start = now - 24*3600", NOW - 24 * 3600, ws)
assert_eq("24h: label", "24h", label)

ws, we, label = u.resolve_window("3m", now=NOW)
assert_eq("3m: ~90 days", NOW - 3 * 30 * 86400, ws)
assert_eq("3m: label", "3m", label)

ws, we, label = u.resolve_window("1y", now=NOW)
assert_eq("1y: ~365 days", NOW - 365 * 86400, ws)

ws, we, label = u.resolve_window("all", now=NOW)
assert_eq("all: window_start = 0 (unbounded)", 0, ws)
assert_eq("all: label", "all", label)

ws, we, label = u.resolve_window("lifetime", now=NOW)
assert_eq("lifetime: window_start = 0", 0, ws)
if "lifetime" in label and "local" in label:
    ok("lifetime: labeled as local retained")
else:
    fail("lifetime: labeled as local retained", label)

ws, we, label = u.resolve_window("", now=NOW)
assert_eq("empty spec treated as 'all'", 0, ws)

ws, we, label = u.resolve_window("today", now=NOW)
assert True if we == NOW else fail("today: window_end = now", f"{we} != {NOW}")
if ws <= NOW and (NOW - ws) <= 86400:
    ok("today: window_start is within the last 24h (local midnight)")
else:
    fail("today: window_start is within the last 24h (local midnight)", f"ws={ws} now={NOW}")

ws, we, label = u.resolve_window("bogus-spec", now=NOW)
if "unrecognized" in label and (we - ws) == 7 * 24 * 3600:
    ok("unrecognized window spec: honestly-labeled 7d fallback, never silent/'all'")
else:
    fail("unrecognized window spec: honestly-labeled 7d fallback", f"label={label!r} span={we - ws}")

print("== lib/usage_ingest.py: filter_rows() ==")
rows = [
    u.UsageRow(NOW - 100, "anthropic", "claude-opus-4-8", "p", 1, 1, 0, 0),
    u.UsageRow(NOW - 999999, "anthropic", "claude-opus-4-8", "p", 1, 1, 0, 0),  # far outside
]
filtered = u.filter_rows(rows, NOW - 3600, NOW)
assert_eq("filter_rows keeps only in-window rows", 1, len(filtered))

print("== lib/usage_ingest.py: aggregate() (synthetic in-memory rows) ==")
synth_rows = [
    u.UsageRow(NOW - 3600, "anthropic", "claude-opus-4-8", "proj-a", 100, 50, 10, 5),
    u.UsageRow(NOW - 7200, "openai", "gpt-5", "proj-b", 150, 60, 0, 0),
]
agg = u.aggregate(synth_rows, NOW - 7 * 86400, NOW, "7d")
assert_eq("aggregate: total_events", 2, agg["total_events"])
assert_eq("aggregate: totals.total_tokens", 165 + 210, agg["totals"]["total_tokens"])
assert_eq("aggregate: by_provider keys", {"anthropic", "openai"}, set(agg["by_provider"].keys()))
assert_eq("aggregate: by_provider anthropic total", 165, agg["by_provider"]["anthropic"]["total_tokens"])
assert_eq("aggregate: by_model keys", {"claude-opus-4-8", "gpt-5"}, set(agg["by_model"].keys()))
assert_eq("aggregate: by_project keys", {"proj-a", "proj-b"}, set(agg["by_project"].keys()))
assert_eq("aggregate: window label carried through", "7d", agg["window"])

empty_agg = u.aggregate([], NOW - 3600, NOW, "1h")
assert_eq("aggregate: empty rows -> total_events 0", 0, empty_agg["total_events"])
assert_eq("aggregate: empty rows -> zeroed totals", 0, empty_agg["totals"]["total_tokens"])
assert_eq("aggregate: empty rows -> empty breakdowns", {}, empty_agg["by_provider"])

print("== lib/usage_ingest.py: collect() over the synthetic fixture tree ==")
agg = u.collect("claude-code", str(FIXTURE_DIR), WINDOW, now=NOW)

# In-window rows: original 5 shallow + 2 nested (haiku 60 + sonnet 45 under
# alpha). 200h-old outlier still excluded. Nested subagent paths prove the
# recursive scan (Sonnet/Haiku no longer invisible).
assert_eq("collect: total_events (window-filtered + nested subagent rows)", 7, agg["total_events"])
assert_eq("collect: sources_scanned (all parsed rows before window filter)", 8, agg["sources_scanned"])
assert_eq("collect: totals.input_tokens", 100 + 200 + 150 + 90 + 10 + 40 + 30, agg["totals"]["input_tokens"])
assert_eq("collect: totals.output_tokens", 50 + 80 + 60 + 30 + 10 + 20 + 15, agg["totals"]["output_tokens"])
assert_eq("collect: totals.cache_read_tokens (only line 1 has any)", 10, agg["totals"]["cache_read_tokens"])
assert_eq("collect: totals.cache_creation_tokens (only line 1 has any)", 5, agg["totals"]["cache_creation_tokens"])
assert_eq(
    "collect: by_provider breakdown (anthropic/openai/google/other all present)",
    {"anthropic", "openai", "google", "other"},
    set(agg["by_provider"].keys()),
)
# anthropic: opus 165 + sonnet shallow 280 + haiku 60 + sonnet nested 45 = 550
assert_eq("collect: by_provider anthropic total (opus+sonnet+haiku nested)", 550, agg["by_provider"]["anthropic"]["total_tokens"])
assert_eq(
    "collect: by_project breakdown (both fake projects present)",
    {"-fake-project-alpha", "-fake-project-beta"},
    set(agg["by_project"].keys()),
)
assert_eq("collect: by_project alpha total (opus+sonnet+nested)", 550, agg["by_project"]["-fake-project-alpha"]["total_tokens"])
assert_eq(
    "collect: by_project beta total (gpt-5 210 + gemini 120 + mystery 20)",
    350,
    agg["by_project"]["-fake-project-beta"]["total_tokens"],
)
if "claude-haiku-4-5-20251001" in agg["by_model"] and "claude-sonnet-5" in agg["by_model"]:
    ok("collect: nested sonnet+haiku models present (recursive scan)")
else:
    fail("collect: nested sonnet+haiku models present", sorted(agg["by_model"].keys()))
assert_eq("collect: never raises, no 'skipped' reason for a good source", None, agg.get("skipped"))
assert_eq("collect: echoes back the source name", "claude-code", agg["source"])

print("== lib/usage_ingest.py: collect() is skip-graceful (opt-in, best-effort, never fabricates) ==")
unknown_agg = u.collect("not-a-real-adapter", str(FIXTURE_DIR), WINDOW, now=NOW)
if unknown_agg.get("skipped") and "unknown usage source adapter" in unknown_agg["skipped"]:
    ok("collect: unknown source adapter -> honest 'skipped' reason, not a raise")
else:
    fail("collect: unknown source adapter -> honest 'skipped' reason", unknown_agg)
assert_eq("collect: unknown adapter -> total_events 0 (never fabricated)", 0, unknown_agg["total_events"])

with tempfile.TemporaryDirectory() as tmpdir:
    missing_agg = u.collect("claude-code", str(Path(tmpdir) / "does-not-exist"), WINDOW, now=NOW)
if missing_agg.get("skipped") and "projects_dir not found" in missing_agg["skipped"]:
    ok("collect: absent projects_dir -> honest 'skipped' reason, not a raise")
else:
    fail("collect: absent projects_dir -> honest 'skipped' reason", missing_agg)
assert_eq("collect: absent projects_dir -> total_events 0", 0, missing_agg["total_events"])

print("== lib/usage_ingest.py: CLI (writes the gitignored JSON cache) ==")
import json  # noqa: E402
import subprocess  # noqa: E402


# The CLI has no `now` override (by design - production always uses the
# real clock), so a "7d" window would be relative to the REAL wall clock,
# which doesn't cover the fixture tree's fixed-2023 timestamps. Use "all"
# here instead - unbounded, so it's deterministic regardless of when this
# test runs, and cross-check against collect()'s own "all" aggregate
# (also real-clock, but window_start=0 makes "now" irrelevant).
all_agg = u.collect("claude-code", str(FIXTURE_DIR), "all")

with tempfile.TemporaryDirectory() as tmpdir:
    out_json = str(Path(tmpdir) / "usage-summary.json")
    cli = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "usage_ingest.py"), "claude-code", str(FIXTURE_DIR), "all", out_json],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert_eq("CLI exits 0 on a normal collection", 0, cli.returncode)
    if cli.stdout.startswith("OK:"):
        ok("CLI prints OK:<path> on a normal collection")
    else:
        fail("CLI prints OK:<path> on a normal collection", cli.stdout)
    if Path(out_json).is_file():
        with open(out_json) as f:
            cached = json.load(f)
        assert_eq("CLI-written cache has the expected total_tokens", all_agg["totals"]["total_tokens"], cached["totals"]["total_tokens"])
    else:
        fail("CLI writes the output JSON cache file", "file not created")

    out_json2 = str(Path(tmpdir) / "usage-summary-skip.json")
    cli2 = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "usage_ingest.py"), "claude-code", str(Path(tmpdir) / "nope"), WINDOW, out_json2],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert_eq("CLI exits 0 even when the source is absent (never a hard failure)", 0, cli2.returncode)
    if cli2.stdout.startswith("SKIP:"):
        ok("CLI prints SKIP:<reason> when the source is absent")
    else:
        fail("CLI prints SKIP:<reason> when the source is absent", cli2.stdout)
    if Path(out_json2).is_file():
        ok("CLI still writes an (honestly empty) cache file on SKIP")
    else:
        fail("CLI still writes a cache file on SKIP", "file not created")

cli_bad_args = subprocess.run(
    [sys.executable, str(REPO_ROOT / "lib" / "usage_ingest.py"), "claude-code"],
    capture_output=True,
    text=True,
    timeout=10,
)
assert_eq("CLI exits non-zero ONLY for a genuine bad invocation (wrong arg count)", 2, cli_bad_args.returncode)

print("== lib/dashboard_render.py: usage panels + text fallback (import-level, no matplotlib required) ==")
import dashboard_render as dr  # noqa: E402

disabled_text = dr._render_usage_text(None)
if "unavailable" in disabled_text:
    ok("_render_usage_text(None) is honest about no usage data (disabled/no source)")
else:
    fail("_render_usage_text(None) is honest about no usage data", disabled_text)

populated_text = dr._render_usage_text(agg)
# Project names are truncated to 16 chars by metrics_agg._bar_section's
# shared bar-list renderer (same behavior/limit as the relay dashboard's
# hook/command breakdowns) - check the truncated-but-still-identifying
# prefix, not the full fixture name.
if "claude-opus-4-8" in populated_text and "gpt-5" in populated_text and "-fake-project-al" in populated_text:
    ok("_render_usage_text(agg) includes provider/model/project breakdowns")
else:
    fail("_render_usage_text(agg) includes provider/model/project breakdowns", populated_text)

toggled_text = dr._render_usage_text(agg, show_providers=False, show_models=False)
if "display disabled" in toggled_text and "claude-opus-4-8" not in toggled_text:
    ok("_render_usage_text(): providers/models=False labels the section disabled, omits the data")
else:
    fail("_render_usage_text(): providers/models=False labels the section disabled", toggled_text)

assert_eq("_fmt_tokens: small counts pass through", "500", dr._fmt_tokens(500))
assert_eq("_fmt_tokens: thousands compact", "12.3k", dr._fmt_tokens(12345))
assert_eq("_fmt_tokens: millions compact", "1.2M", dr._fmt_tokens(1234567))
assert_eq("_fmt_tokens: non-numeric never raises", "0", dr._fmt_tokens("not-a-number"))

assert_eq("_top_key: empty dict -> None, never fabricated", None, dr._top_key({}))
assert_eq(
    "_top_key: picks the largest total_tokens",
    "b",
    dr._top_key({"a": {"total_tokens": 5}, "b": {"total_tokens": 50}}),
)

print("== lib/dashboard_render.py: usage image path when matplotlib IS available, else graceful TEXT ==")
try:
    import matplotlib  # noqa: F401

    HAS_MPL = True
except ImportError:
    HAS_MPL = False

with tempfile.TemporaryDirectory() as tmpdir:
    usage_cache = str(Path(tmpdir) / "usage-summary.json")
    with open(usage_cache, "w", encoding="utf-8") as f:
        json.dump(agg, f)
    out_png = str(Path(tmpdir) / "usage.png")
    rc = dr.main(["dashboard_render.py", "--usage-only", usage_cache, out_png])
    assert_eq("dashboard_render.py --usage-only never exits non-zero for a data/render condition", 0, rc)

if HAS_MPL:
    print("      (matplotlib present in this interpreter - exercising the usage IMAGE paths)")
    with tempfile.TemporaryDirectory() as tmpdir:
        out_png = str(Path(tmpdir) / "usage-only.png")
        rendered = dr._render_usage_image(agg, out_png)
        if rendered and Path(out_png).is_file() and Path(out_png).stat().st_size > 0:
            ok("_render_usage_image() writes a non-empty PNG")
        else:
            fail("_render_usage_image() writes a non-empty PNG")

    with tempfile.TemporaryDirectory() as tmpdir:
        out_png = str(Path(tmpdir) / "toggled.png")
        rendered = dr._render_usage_image(agg, out_png, show_providers=False, show_models=False)
        if rendered and Path(out_png).is_file() and Path(out_png).stat().st_size > 0:
            ok("_render_usage_image() with providers/models disabled still writes a non-empty PNG (placeholder panels)")
        else:
            fail("_render_usage_image() with providers/models disabled still writes a non-empty PNG")

    # Extending the RELAY dashboard with usage panels appended - the
    # /dashboard-with-usage-enabled path, using the real metrics fixture
    # (tests/fixtures/metrics-synthetic.log) alongside the usage fixture.
    import metrics_agg

    metrics_fixture = REPO_ROOT / "tests" / "fixtures" / "metrics-synthetic.log"
    m_rows = metrics_agg.parse_log(str(metrics_fixture))
    m_filtered, mw_start, mw_end = metrics_agg.filter_window(m_rows, 1, now=1000002000)
    m_agg = metrics_agg.aggregate(m_filtered, 1, mw_start, mw_end)

    with tempfile.TemporaryDirectory() as tmpdir:
        out_png = str(Path(tmpdir) / "combined.png")
        rendered = dr._render_image(m_agg, out_png, usage_agg=agg)
        if rendered and Path(out_png).is_file() and Path(out_png).stat().st_size > 0:
            ok("_render_image() with usage_agg appends usage panels and still writes a non-empty PNG")
        else:
            fail("_render_image() with usage_agg appends usage panels and still writes a non-empty PNG")

    with tempfile.TemporaryDirectory() as tmpdir:
        out_png = str(Path(tmpdir) / "no-usage.png")
        rendered = dr._render_image(m_agg, out_png, usage_agg=None)
        if rendered and Path(out_png).is_file() and Path(out_png).stat().st_size > 0:
            ok("_render_image() with usage_agg=None (disabled) renders the relay dashboard unchanged")
        else:
            fail("_render_image() with usage_agg=None (disabled) renders the relay dashboard unchanged")
else:
    print("SKIP  matplotlib not installed in this interpreter - usage IMAGE paths not exercised here")
    print("      (never-silent: this line IS the record; the TEXT fallback above IS covered)")

# ============================================================================
print()
print("=" * 60)
print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
if FAIL > 0:
    print(f"FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
sys.exit(0)

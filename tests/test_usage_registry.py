#!/usr/bin/env python3
"""tests/test_usage_registry.py - Usage adapters come from providers registry.

Verifies issue #31: ADAPTERS is filled primarily from providers/* usage
collectors (via usage_ingest._register_provider_usage_adapters), while the
stable source keys "claude-code" and "grok" keep working, and collect()
still reads the synthetic Claude Code fixture tree.

Run: python3.14 tests/test_usage_registry.py  (or python3)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Providers first (registry), then lib/ for usage_ingest.
sys.path.insert(0, str(REPO_ROOT / "lib"))
sys.path.insert(0, str(REPO_ROOT))

import providers  # noqa: F401
import usage_ingest as u
from providers.base import list_providers

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


print("== usage_ingest.ADAPTERS from providers registry ==")
assert_eq("claude-code in ADAPTERS", True, "claude-code" in u.ADAPTERS)
assert_eq("grok in ADAPTERS", True, "grok" in u.ADAPTERS)
assert_eq("ADAPTERS claude-code is callable", True, callable(u.ADAPTERS.get("claude-code")))
assert_eq("ADAPTERS grok is callable", True, callable(u.ADAPTERS.get("grok")))

# Prefer providers/*.usage_collect when registered.
by_source = {p.usage_source: p for p in list_providers() if p.usage_source and p.usage_collect}
if "claude-code" in by_source:
    assert_eq(
        "claude-code adapter is providers.claude collect_usage",
        by_source["claude-code"].usage_collect,
        u.ADAPTERS["claude-code"],
    )
else:
    fail("claude-code adapter is providers.claude collect_usage", "no provider registered")
if "grok" in by_source:
    assert_eq(
        "grok adapter is providers.grok collect_usage",
        by_source["grok"].usage_collect,
        u.ADAPTERS["grok"],
    )
else:
    fail("grok adapter is providers.grok collect_usage", "no provider registered")

print("== provider_catalog usage-sources lists both ==")
catalog = subprocess.run(
    [sys.executable, str(REPO_ROOT / "lib" / "provider_catalog.py"), "usage-sources"],
    capture_output=True,
    text=True,
    timeout=10,
    cwd=str(REPO_ROOT),
)
assert_eq("provider_catalog usage-sources exits 0", 0, catalog.returncode)
try:
    rows = json.loads(catalog.stdout)
except json.JSONDecodeError as e:
    rows = []
    fail("provider_catalog usage-sources JSON", str(e))
sources = {r.get("usage_source") for r in rows if isinstance(r, dict)}
assert_eq("usage-sources includes claude-code", True, "claude-code" in sources)
assert_eq("usage-sources includes grok", True, "grok" in sources)

print("== collect still works on usage-synthetic for claude-code ==")
NOW = 1700000000
agg = u.collect("claude-code", str(FIXTURE_DIR), "7d", now=NOW)
assert_eq("collect: no skipped on fixture tree", None, agg.get("skipped"))
# Same window-filtered count as test_usage_ingest.py (7 in-window rows).
assert_eq("collect: total_events on synthetic fixture", 7, agg["total_events"])
assert_eq("collect: sources_scanned before window filter", 8, agg["sources_scanned"])
assert_eq("collect: source echo", "claude-code", agg["source"])

print()
print("=" * 60)
print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
if FAIL > 0:
    print(f"FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
sys.exit(0)

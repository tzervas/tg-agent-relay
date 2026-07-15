#!/usr/bin/env python3
"""tests/test_usage_registry.py - Usage adapters come from providers registry.

Verifies issue #31:
  - ADAPTERS is filled from providers/* usage collectors
    (usage_ingest.refresh_usage_adapters)
  - Stable source keys "claude-code" and "grok" keep working
  - collect() still reads the synthetic Claude Code fixture tree
  - A fake Provider with usage_collect appears in the collect path after refresh
  - provider_catalog usage-sources lists registered sources

Run: uv run python tests/test_usage_registry.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    global PASS, FAIL
    PASS = 0
    FAIL = 0
    sys.path.insert(0, str(REPO_ROOT / "lib"))
    sys.path.insert(0, str(REPO_ROOT))
    import providers.base as pb
    import usage_ingest as u
    from providers.base import Provider, list_providers, register

    FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "usage-synthetic"
    NOW = 1700000000
    FAKE_PROVIDER_ID = "issue31-fake-harness"
    FAKE_SOURCE = "fake-harness"
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
    by_source = {p.usage_source: p for p in list_providers() if p.usage_source and p.usage_collect}
    if "claude-code" in by_source:
        assert_eq(
            "claude-code adapter is providers.claude collect_usage (registry, not local fallback)",
            by_source["claude-code"].usage_collect,
            u.ADAPTERS["claude-code"],
        )
        assert_eq(
            "claude-code adapter is NOT the local _collect_claude_code fallback",
            True,
            u.ADAPTERS["claude-code"] is not u._collect_claude_code,
        )
    else:
        fail(
            "claude-code adapter is providers.claude collect_usage (registry, not local fallback)",
            "no provider registered",
        )
    if "grok" in by_source:
        assert_eq(
            "grok adapter is providers.grok collect_usage (registry, not local fallback)",
            by_source["grok"].usage_collect,
            u.ADAPTERS["grok"],
        )
        assert_eq(
            "grok adapter is NOT the local _collect_grok fallback",
            True,
            u.ADAPTERS["grok"] is not u._collect_grok,
        )
    else:
        fail(
            "grok adapter is providers.grok collect_usage (registry, not local fallback)",
            "no provider registered",
        )
    print("== provider_catalog usage-sources lists all ==")
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
    for p in list_providers():
        if p.usage_source:
            if p.usage_source in sources:
                ok(f"usage-sources includes registered source {p.usage_source!r}")
            else:
                fail(
                    f"usage-sources includes registered source {p.usage_source!r}",
                    f"catalog sources={sorted(sources)}",
                )
    print("== collect still works on usage-synthetic for claude-code ==")
    agg = u.collect("claude-code", str(FIXTURE_DIR), "7d", now=NOW)
    assert_eq("collect: no skipped on fixture tree", None, agg.get("skipped"))
    assert_eq("collect: total_events on synthetic fixture", 7, agg["total_events"])
    assert_eq("collect: sources_scanned before window filter", 8, agg["sources_scanned"])
    assert_eq("collect: source echo", "claude-code", agg["source"])
    print("== fake Provider injects collector into collect path (issue #31 AC) ==")
    fake_rows_emitted: list[u.UsageRow] = []

    def fake_collect(base: Path) -> list[u.UsageRow]:
        """Synthetic collector — proves registry injection, not filesystem scan."""
        row = u.UsageRow(
            ts=NOW - 60,
            provider="fake",
            model="fake-model-1",
            project="fake-project",
            input_tokens=42,
            output_tokens=7,
            cache_read_tokens=0,
            cache_creation_tokens=0,
        )
        fake_rows_emitted.append(row)
        _ = base.is_dir()
        return [row]

    pb._REGISTRY.pop(FAKE_PROVIDER_ID, None)
    try:
        register(
            Provider(
                id=FAKE_PROVIDER_ID,
                display_name="Fake Harness (issue #31)",
                config_namespace="fake_harness",
                backend_id="fake",
                usage_source=FAKE_SOURCE,
                usage_default_dir="/tmp/fake-usage",
                usage_collect=fake_collect,
                model_prefixes=("fake-model",),
                provider_label="fake",
            )
        )
        u.refresh_usage_adapters()
        assert_eq("fake source appears in ADAPTERS after refresh", True, FAKE_SOURCE in u.ADAPTERS)
        assert_eq(
            "ADAPTERS[fake] is the injected collector (identity)",
            fake_collect,
            u.ADAPTERS.get(FAKE_SOURCE),
        )
        assert_eq(
            "claude-code still registry-backed after refresh",
            True,
            u.ADAPTERS.get("claude-code") is not u._collect_claude_code,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            agg_fake = u.collect(FAKE_SOURCE, tmpdir, "all", now=NOW)
        if agg_fake.get("skipped"):
            fail("fake collect: no skipped", str(agg_fake.get("skipped")))
        else:
            ok("fake collect: no skipped")
        assert_eq("fake collect: total_events", 1, agg_fake["total_events"])
        assert_eq(
            "fake collect: input_tokens from injected collector",
            42,
            agg_fake["totals"]["input_tokens"],
        )
        assert_eq(
            "fake collect: output_tokens from injected collector",
            7,
            agg_fake["totals"]["output_tokens"],
        )
        assert_eq(
            "fake collect: by_model includes fake-model-1",
            True,
            "fake-model-1" in agg_fake["by_model"],
        )
        assert_eq("fake collect: source echo", FAKE_SOURCE, agg_fake["source"])
        assert_eq("fake collector was invoked", True, len(fake_rows_emitted) >= 1)
        live_sources = {
            p.usage_source for p in list_providers() if p.usage_source and p.usage_collect
        }
        assert_eq(
            "list_providers includes fake usage_source after register",
            True,
            FAKE_SOURCE in live_sources,
        )
    finally:
        pb._REGISTRY.pop(FAKE_PROVIDER_ID, None)
        u.refresh_usage_adapters()
        if FAKE_SOURCE not in u.ADAPTERS:
            ok("refresh after unregister drops fake source from ADAPTERS")
        else:
            fail(
                "refresh after unregister drops fake source from ADAPTERS",
                f"still present: {u.ADAPTERS.get(FAKE_SOURCE)}",
            )
        assert_eq(
            "claude-code restored after fake cleanup",
            True,
            "claude-code" in u.ADAPTERS and callable(u.ADAPTERS["claude-code"]),
        )
        assert_eq(
            "grok restored after fake cleanup",
            True,
            "grok" in u.ADAPTERS and callable(u.ADAPTERS["grok"]),
        )
    print()
    print("=" * 60)
    print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
    return 0 if FAIL == 0 else 1


def test_usage_registry() -> None:
    """pytest entry — dual-runs the standalone script checks."""
    rc = main()
    assert rc == 0, f"script-style checks failed (exit {rc}); see PASS/FAIL lines above"


if __name__ == "__main__":
    raise SystemExit(main())

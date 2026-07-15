#!/usr/bin/env python3
"""Offline tests for plug-and-play provider registry (OpenAI + discovery)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lib"))

import providers  # noqa: F401
from providers.base import (
    CAP_DELIVERY,
    CAP_HOOKS,
    CAP_OPENAI_COMPAT,
    CAP_USAGE,
    get_provider,
    infer_provider_label,
    list_providers,
    provider_for_backend_type,
    providers_with_capability,
)

PASS = 0
FAIL = 0


def ok(name: str) -> None:
    global PASS
    PASS += 1
    print(f"PASS  {name}")


def fail(name: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    print(f"FAIL  {name}")
    if detail:
        print(f"      {detail}")


def eq(name: str, expected, actual) -> None:
    if expected == actual:
        ok(name)
    else:
        fail(name, f"expected={expected!r} actual={actual!r}")


def true(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail or "condition false")


def main() -> int:
    ids = {p.id for p in list_providers()}
    for need in ("grok", "claude", "openai", "ollama", "gemini", "aider", "generic"):
        true(f"registry has {need}", need in ids)

    oai = get_provider("openai")
    true("openai provider present", oai is not None)
    assert oai is not None
    true("openai has delivery", oai.has(CAP_DELIVERY))
    true("openai has openai_compat", oai.has(CAP_OPENAI_COMPAT))
    true("openai has usage", oai.has(CAP_USAGE))
    true("openai no hooks (yet)", not oai.has(CAP_HOOKS) or len(oai.hook_events) == 0)
    true("openai presets non-empty", len(oai.delivery_presets) >= 3)
    true(
        "openai backend types include compat",
        "openai-compat" in oai.backend_types and "vllm" in oai.backend_types,
    )

    eq("backend-type vllm → openai", "openai", provider_for_backend_type("vllm").id)  # type: ignore[union-attr]
    eq("backend-type lmstudio → openai", "openai", provider_for_backend_type("lmstudio").id)  # type: ignore[union-attr]
    eq("backend-type ollama → ollama", "ollama", provider_for_backend_type("ollama").id)  # type: ignore[union-attr]
    eq("backend-type gemini → gemini", "gemini", provider_for_backend_type("gemini").id)  # type: ignore[union-attr]

    eq("model gpt-4o → openai", "openai", infer_provider_label("gpt-4o"))
    eq("model o1-preview → openai", "openai", infer_provider_label("o1-preview"))
    eq(
        "model claude-sonnet → anthropic",
        "anthropic",
        infer_provider_label("claude-3-5-sonnet-20241022"),
    )
    eq("model llama3 → ollama", "ollama", infer_provider_label("llama3.2"))

    # Refresh usage adapters after openai registered
    import usage_ingest  # type: ignore

    usage_ingest.refresh_usage_adapters()
    true("ADAPTERS has openai", "openai" in usage_ingest.ADAPTERS)
    true("ADAPTERS has ollama", "ollama" in usage_ingest.ADAPTERS)

    hook_ids = {p.id for p in providers_with_capability(CAP_HOOKS)}
    true("grok in hooks cap", "grok" in hook_ids)
    true("claude in hooks cap", "claude" in hook_ids)
    true(
        "openai not in hooks (no catalog)",
        "openai" not in hook_ids or len(get_provider("openai").hook_events) == 0,
    )

    # catalog CLI
    import subprocess

    r = subprocess.run(
        [sys.executable, str(REPO / "lib/provider_catalog.py"), "list"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=30,
    )
    eq("catalog list exit 0", 0, r.returncode)
    rows = json.loads(r.stdout)
    true("catalog lists openai", any(x.get("id") == "openai" for x in rows))

    r2 = subprocess.run(
        [sys.executable, str(REPO / "lib/provider_catalog.py"), "presets", "openai"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=30,
    )
    eq("catalog presets exit 0", 0, r2.returncode)
    presets = json.loads(r2.stdout)
    true(
        "presets include openai-compat",
        any(p.get("backend_type") == "openai-compat" for p in presets),
    )

    print()
    print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

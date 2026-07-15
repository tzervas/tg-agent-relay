#!/usr/bin/env python3
"""tests/test_context_select.py — Exclusive hybrid context selection (#29 / #36).

Ports the hybrid context assertions from tests/run-tests.sh into a pure-Python
pytest dual-run module (no jq, no network).

Rules under test (docs/context + AGENTS.md):
  - vision=True  → image modality (+ caption); text twin in do_not_load
  - vision=False → text modality only; image twin in do_not_load
  - never load both modalities for the same manifest id (no double-dip)
  - no selected path appears in the same item's do_not_load list

Run:
  uv run pytest tests/test_context_select.py
  uv run python tests/test_context_select.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

import context_select as cs

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


def true(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail)


def main() -> int:
    global PASS, FAIL, FAILURES
    PASS = FAIL = 0
    FAILURES = []

    manifest_path = REPO / "docs" / "context" / "manifest.json"
    true("manifest exists", manifest_path.is_file(), str(manifest_path))
    if not manifest_path.is_file():
        print()
        print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
        return 1

    try:
        manifest = cs.load_manifest(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as _exc:
        fail("load_manifest", str(_exc))
        print()
        print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
        return 1
    ok("load_manifest")

    entries = manifest.get("entries") or []
    true("manifest has entries", len(entries) >= 1, repr(entries))

    # --- vision mode ---
    vis = cs.select_context(manifest, vision=True, repo_root=REPO)
    true("vision mode label", vis.get("mode") == "visual", repr(vis.get("mode")))
    true("vision selection_rule exclusive", vis.get("selection_rule") == "exclusive_by_capability")
    vis_items = vis.get("items") or []
    true("vision returns items", len(vis_items) >= 1, repr(vis_items))

    modalities = {item.get("modality") for item in vis_items}
    # Prefer images; text only allowed as missing-image fallback
    true(
        "vision modalities are image (or text fallback)",
        modalities <= {"image", "text"},
        repr(modalities),
    )
    for item in vis_items:
        dnl = item.get("do_not_load") or []
        path = item.get("path")
        true(
            f"vision item {item.get('id')}: path not in do_not_load",
            path not in dnl,
            repr(item),
        )
        if item.get("modality") == "image":
            true(
                f"vision image {item.get('id')}: lists text twin in do_not_load",
                len(dnl) > 0,
                repr(item),
            )
            true(
                f"vision image {item.get('id')}: has caption field",
                "caption" in item,
                repr(item),
            )

    # --- text / no-vision mode ---
    nov = cs.select_context(manifest, vision=False, repo_root=REPO)
    true("no-vision mode label", nov.get("mode") == "text", repr(nov.get("mode")))
    nov_items = nov.get("items") or []
    true("no-vision returns items", len(nov_items) >= 1, repr(nov_items))
    true(
        "no-vision all text modality",
        all(i.get("modality") == "text" for i in nov_items),
        repr([i.get("modality") for i in nov_items]),
    )
    for item in nov_items:
        dnl = item.get("do_not_load") or []
        path = item.get("path")
        true(
            f"text item {item.get('id')}: path not in do_not_load",
            path not in dnl,
            repr(item),
        )
        # entries with an image twin must exclude it
        entry = next((e for e in entries if e.get("id") == item.get("id")), {})
        if entry.get("image"):
            true(
                f"text item {item.get('id')}: image twin in do_not_load",
                str(entry["image"]) in dnl,
                repr(item),
            )

    # --- exclusive: same id never appears twice with both modalities ---
    for result, label in ((vis, "vision"), (nov, "no-vision")):
        by_id: dict[str, list[str]] = {}
        for item in result.get("items") or []:
            by_id.setdefault(str(item.get("id")), []).append(str(item.get("modality")))
        for eid, mods in by_id.items():
            true(
                f"{label}: id {eid!r} single modality (no double-dip)",
                len(mods) == 1,
                repr(mods),
            )

    # --- CLI smoke (capture stdout so dual-run under pytest stays quiet) ---
    import contextlib
    import io

    for label, argv in (
        ("--vision", ["--vision", "--repo-root", str(REPO)]),
        ("--no-vision", ["--no-vision", "--repo-root", str(REPO)]),
        ("--list-ids", ["--list-ids", "--repo-root", str(REPO)]),
    ):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cs.main(argv)
        true(f"CLI {label} exit 0", rc == 0, str(rc))
        true(
            f"CLI {label} prints JSON object",
            buf.getvalue().lstrip().startswith("{"),
            buf.getvalue()[:200],
        )

    print()
    print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
    if FAILURES:
        print("Failed:", ", ".join(FAILURES))
    return 0 if FAIL == 0 else 1


def test_context_select() -> None:
    """pytest entry — dual-runs the standalone script checks."""
    rc = main()
    assert rc == 0, f"script-style checks failed (exit {rc}); see PASS/FAIL lines above"


if __name__ == "__main__":
    raise SystemExit(main())

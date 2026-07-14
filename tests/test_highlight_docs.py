#!/usr/bin/env python3
"""Offline tests for Python code-highlight document queue (#57)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lib"))

from tg_agent_relay.highlight_docs import (  # noqa: E402
    build_code_doc_jobs,
    extract_fenced_blocks,
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


def true(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail or "false")


def eq(name: str, a, b) -> None:
    if a == b:
        ok(name)
    else:
        fail(name, f"{a!r} != {b!r}")


def main() -> int:
    blocks = extract_fenced_blocks("hello\n```python\nprint(1)\n```\nbye")
    eq("one fence", 1, len(blocks))
    eq("lang python", "python", blocks[0][0])
    true("body has print", "print(1)" in blocks[0][1])

    unclosed = extract_fenced_blocks("```rust\nfn main() {}")
    eq("unclosed ignored", 0, len(unclosed))

    # mode off → no jobs
    jobs = build_code_doc_jobs(
        "```python\nx=1\n```",
        config={"code_highlight": {"mode": "inline-only"}},
    )
    eq("inline-only no jobs", 0, len(jobs))

    # html-doc: jobs only if pygments present
    try:
        import pygments  # noqa: F401

        has_pyg = True
    except ImportError:
        has_pyg = False

    jobs2 = build_code_doc_jobs(
        "```python\nprint('hi')\n```",
        config={"code_highlight": {"mode": "html-doc", "keep_text": "none"}},
    )
    if has_pyg:
        true("html-doc produces job", len(jobs2) >= 1)
        if jobs2:
            true("html file exists", jobs2[0].path.is_file())
            eq("caption none", "", jobs2[0].caption)
            for j in jobs2:
                j.path.unlink(missing_ok=True)
    else:
        eq("no pygments → no jobs", 0, len(jobs2))

    print()
    print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

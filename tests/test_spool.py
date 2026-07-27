#!/usr/bin/env python3
"""tests/test_spool.py — Offline unit tests for tg_agent_relay.spool.

The spool is the recovery layer behind ``message_orphaned``: when no agent
Monitor holds a backend FIFO, inbound lines are persisted here instead of being
written into a keepalive-held kernel buffer where no agent ever sees them.

Covers:
  - ordering (chronological replay, not arbitrary readdir order)
  - atomic claim (no double-delivery under concurrent drains)
  - emit failure leaves the line pending (never consumed on error)
  - stale claim reclaim after a drainer dies mid-flight
  - bounded spool drops oldest and says so (never silently)
  - backend id sanitation (no path escape from relay.toml)
  - partially written files are never drained

NO network. Stdlib-only PASS/FAIL runner.
Run:  python3 tests/test_spool.py
      uv run python tests/test_spool.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tg_agent_relay.spool import (  # noqa: E402
    drain,
    pending_count,
    pending_paths,
    reclaim_stale,
    spool_dir,
    spool_message,
)

PASS = FAIL = 0


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


def eq(name: str, exp, act) -> None:
    if exp == act:
        ok(name)
    else:
        fail(name, f"expected {exp!r} got {act!r}")


def true(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail)


def _tmp_bridge() -> Path:
    return Path(tempfile.mkdtemp(prefix="tg-spool-test-"))


def main() -> int:
    # --- round trip preserves content and order ----------------------------
    b = _tmp_bridge()
    for i in range(5):
        spool_message("fleet", f"[telegram:fleet] line {i}", b)
    eq("all lines pending", 5, pending_count("fleet", b))

    seen: list[str] = []
    eq("drain returns count", 5, drain("fleet", seen.append, b))
    eq(
        "replay is chronological",
        [f"[telegram:fleet] line {i}" for i in range(5)],
        seen,
    )
    eq("spool empty after drain", 0, pending_count("fleet", b))
    eq("second drain is a no-op", 0, drain("fleet", seen.append, b))

    # Trailing newline is normalized away so drain re-adds exactly one.
    b2 = _tmp_bridge()
    spool_message("fleet", "with newline\n", b2)
    got: list[str] = []
    drain("fleet", got.append, b2)
    eq("trailing newline stripped on store", ["with newline"], got)

    # --- backends are isolated ---------------------------------------------
    b3 = _tmp_bridge()
    spool_message("fleet", "for fleet", b3)
    spool_message("cabal", "for cabal", b3)
    eq("fleet has its own line", 1, pending_count("fleet", b3))
    eq("cabal has its own line", 1, pending_count("cabal", b3))
    cabal: list[str] = []
    drain("cabal", cabal.append, b3)
    eq("drained only cabal", ["for cabal"], cabal)
    eq("fleet untouched by cabal drain", 1, pending_count("fleet", b3))

    # --- emit failure must not consume the line ----------------------------
    b4 = _tmp_bridge()
    spool_message("fleet", "must survive", b4)

    def boom(_line: str) -> None:
        raise RuntimeError("agent went away mid-write")

    raised = False
    try:
        drain("fleet", boom, b4)
    except RuntimeError:
        raised = True
    true("emit failure propagates", raised)
    eq("line still pending after emit failure", 1, pending_count("fleet", b4))
    recovered: list[str] = []
    drain("fleet", recovered.append, b4)
    eq("line replayable after failure", ["must survive"], recovered)

    # --- limit leaves the remainder pending --------------------------------
    b5 = _tmp_bridge()
    for i in range(4):
        spool_message("fleet", f"m{i}", b5)
    first: list[str] = []
    eq("limit caps the drain", 2, drain("fleet", first.append, b5, limit=2))
    eq("limit took the oldest", ["m0", "m1"], first)
    eq("remainder still pending", 2, pending_count("fleet", b5))

    # --- stale claim reclaim (drainer died mid-flight) ---------------------
    b6 = _tmp_bridge()
    spool_message("fleet", "orphaned by a dead drainer", b6)
    victim = pending_paths("fleet", b6)[0]
    claimed = victim.with_name(f"{victim.name}.claimed.999999")
    os.rename(victim, claimed)
    eq("claimed line is not pending", 0, pending_count("fleet", b6))
    # A fresh claim must NOT be reclaimed — another drainer may be mid-emit.
    eq("fresh claim is left alone", 0, reclaim_stale("fleet", b6, ttl=300))
    # An old claim is returned to pending.
    eq("stale claim reclaimed", 1, reclaim_stale("fleet", b6, ttl=0))
    eq("reclaimed line is pending again", 1, pending_count("fleet", b6))
    back: list[str] = []
    drain("fleet", back.append, b6)
    eq("reclaimed line replays intact", ["orphaned by a dead drainer"], back)

    # --- concurrent drain never double-delivers ----------------------------
    b7 = _tmp_bridge()
    for i in range(6):
        spool_message("fleet", f"once-{i}", b7)
    a_seen: list[str] = []
    b_seen: list[str] = []

    # Interleave two drains: the inner drain runs while the outer holds a claim.
    def outer(line: str) -> None:
        a_seen.append(line)
        drain("fleet", b_seen.append, b7, limit=1, reclaim=False)

    drain("fleet", outer, b7, limit=2, reclaim=False)
    combined = a_seen + b_seen
    eq("no line delivered twice", len(combined), len(set(combined)))
    true(
        "interleaved drains both made progress",
        len(a_seen) > 0 and len(b_seen) > 0,
        f"a={a_seen} b={b_seen}",
    )

    # --- bounded spool drops oldest, and records it ------------------------
    b8 = _tmp_bridge()
    os.environ["RELAY_SPOOL_MAX"] = "3"
    try:
        for i in range(6):
            spool_message("fleet", f"cap{i}", b8)
    finally:
        del os.environ["RELAY_SPOOL_MAX"]
    eq("spool honours the cap", 3, pending_count("fleet", b8))
    kept: list[str] = []
    drain("fleet", kept.append, b8)
    eq("newest lines survive the cap", ["cap3", "cap4", "cap5"], kept)
    metrics = (b8 / ".metrics.log").read_text(encoding="utf-8")
    true("overflow is recorded, not silent", "spool_overflow" in metrics, metrics)

    # --- backend id cannot escape the spool root ---------------------------
    b9 = _tmp_bridge()
    escaped = spool_dir("../../etc", b9)
    true(
        "traversal backend id stays under spool root",
        str(escaped.resolve()).startswith(str((b9 / ".run" / "spool").resolve())),
        str(escaped),
    )
    spool_message("../../etc", "nice try", b9)
    true(
        "no file written outside the bridge dir",
        not (b9.parent / "etc").exists(),
        str(b9.parent),
    )

    # --- a half-written file is never drained ------------------------------
    b10 = _tmp_bridge()
    d = spool_dir("fleet", b10)
    d.mkdir(parents=True, exist_ok=True)
    (d / "0000000000000000001-0000001-0001.tmp").write_text("half written")
    eq("tmp file is not pending", 0, pending_count("fleet", b10))
    never: list[str] = []
    eq("tmp file is never drained", 0, drain("fleet", never.append, b10))

    # --- metrics name the backend and the reason ---------------------------
    b11 = _tmp_bridge()
    spool_message("cabal", "x", b11, reason="no_agent_reader")
    m = (b11 / ".metrics.log").read_text(encoding="utf-8")
    true("spool metric names backend", "backend=cabal" in m, m)
    true("spool metric carries reason", "reason=no_agent_reader" in m, m)
    true("spool metric reports depth", "pending=1" in m, m)

    print()
    print(f"{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def test_spool() -> None:
    """pytest entry point (dual-run with the standalone script form)."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())

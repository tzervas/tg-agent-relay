#!/usr/bin/env python3
"""tests/test_tts_package.py — Offline unit tests for tg_agent_relay.tts.

Covers package-owned spoken_mode short/full helpers (issue #28):
  - strip_formatting / collapse_adjacent_refs re-exports
  - truncate_words (short mode)
  - chunk_text (full mode)
  - prepare_spoken / prepare_spoken_from_config

NO network. Stdlib-only PASS/FAIL runner (same pattern as test_tts_plain_text).
Run:  python3 tests/test_tts_package.py
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tg_agent_relay import tts

PASS = FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  FAIL  {name}  {detail}")


# --- re-exports -------------------------------------------------------------
check("strip_formatting is callable", callable(tts.strip_formatting))
check("collapse_adjacent_refs is callable", callable(tts.collapse_adjacent_refs))

spoken = tts.strip_formatting("Use `secret` and https://example.com/x please")
check("package strip removes backticks", "`" not in spoken, repr(spoken))
check("package strip removes URL host", "example.com" not in spoken, repr(spoken))
check("package strip keeps prose", "Use" in spoken and "please" in spoken, repr(spoken))

collapsed = tts.collapse_adjacent_refs(
    "ref. the message for the code, ref. the message for the code",
    "ref. the message for the code",
)
check(
    "collapse_adjacent_refs re-export collapses pair",
    collapsed.count("ref. the message for the code") == 1,
    repr(collapsed),
)

# --- normalize_spoken_mode --------------------------------------------------
check("normalize empty -> short", tts.normalize_spoken_mode("") == "short")
check("normalize None -> short", tts.normalize_spoken_mode(None) == "short")
check("normalize short -> short", tts.normalize_spoken_mode("short") == "short")
check("normalize full -> full", tts.normalize_spoken_mode("full") == "full")
check("normalize chunk alias -> full", tts.normalize_spoken_mode("chunk") == "full")
check("normalize complete alias -> full", tts.normalize_spoken_mode("COMPLETE") == "full")
check("normalize garbage -> short", tts.normalize_spoken_mode("maybe") == "short")

# --- truncate_words (short mode primitive) ----------------------------------
check(
    "truncate under max is identity",
    tts.truncate_words("hello world", 100) == "hello world",
)
check(
    "truncate max<=0 is identity",
    tts.truncate_words("hello world", 0) == "hello world",
)
check(
    "truncate non-int max is identity",
    tts.truncate_words("hello world", "nope") == "hello world",  # type: ignore[arg-type]
)
TW = tts.truncate_words("one two three four five", 12)
check(
    "truncate word boundary (no mid-word)",
    " " not in TW or TW.endswith(("one", "two", "three", "four", "five")),
    repr(TW),
)
check("truncate result length <= max", len(TW) <= 12, repr(TW))
check("truncate empty stays empty", tts.truncate_words("", 10) == "")
# Exact: first 12 chars "one two thre" → last space cut → "one two"
check(
    "truncate prefers last space",
    tts.truncate_words("one two three four", 12) == "one two",
    repr(tts.truncate_words("one two three four", 12)),
)

# --- chunk_text (full mode primitive) — parity with lib/tts.sh unit tests ---
check(
    "chunk under max -> exactly 1 chunk, byte-identical",
    tts.chunk_text("short text", 100) == ["short text"],
)
check("chunk max=0 -> unbounded single chunk", tts.chunk_text("a b c", 0) == ["a b c"])
check(
    "chunk non-numeric max -> unbounded",
    tts.chunk_text("a b c", "x") == ["a b c"],  # type: ignore[arg-type]
)
check("chunk empty -> 0 chunks", tts.chunk_text("", 10) == [])

LONG = "alpha bravo charlie delta echo foxtrot golf hotel"
chunks = tts.chunk_text(LONG, 20)
check("chunk long text produces >1 chunk", len(chunks) > 1, repr(chunks))
check(
    "no chunk exceeds max_chars",
    all(len(c) <= 20 for c in chunks),
    repr([(c, len(c)) for c in chunks]),
)
rejoined = " ".join(chunks)
check(
    "chunks rejoin to original whitespace-normalized text",
    rejoined == LONG,
    f"rejoined={rejoined!r} orig={LONG!r}",
)

# oversized single word hard-split
LONGWORD = "x" * 25
hard = tts.chunk_text(f"hi {LONGWORD} bye", 10)
check(
    "oversized word hard-split: no chunk > max",
    all(len(c) <= 10 for c in hard),
    repr(hard),
)
check(
    "oversized word content fully preserved",
    LONGWORD in "".join(hard),
    repr(hard),
)
check(
    "leading hi preserved across chunks",
    any(c.startswith("hi") or c == "hi" for c in hard),
    repr(hard),
)
check("trailing bye preserved", any("bye" in c for c in hard), repr(hard))

# --- prepare_spoken short ---------------------------------------------------
PS = tts.prepare_spoken(
    "## Hello\nuse `code` please and more words here to exceed the limit",
    spoken_mode="short",
    spoken_max_chars=30,
)
check("short mode returns SpokenClips", isinstance(PS, tts.SpokenClips))
check("short mode spoken_mode field", PS.spoken_mode == "short")
check("short mode at most one clip", len(PS.clips) <= 1)
check(
    "short mode strips code body",
    all("code" not in c or "ref." in c for c in PS.clips),
    repr(PS.clips),
)
check("short mode strips header markers", all("#" not in c for c in PS.clips), repr(PS.clips))
check("short mode truncated flag when over max", PS.truncated is True, repr(PS))
check("short mode not chunked", PS.chunked is False)
check(
    "short mode spoken_chars is pre-truncate length",
    PS.spoken_chars >= len(PS.clips[0] if PS.clips else ""),
    repr(PS),
)
if PS.clips:
    check("short mode clip length <= max", len(PS.clips[0]) <= 30, repr(PS.clips[0]))

# short under limit
PS2 = tts.prepare_spoken("just a short note", spoken_mode="short", spoken_max_chars=600)
check("short under max: not truncated", PS2.truncated is False, repr(PS2))
check("short under max: one clip", len(PS2.clips) == 1, repr(PS2.clips))
check("short under max: prose kept", "just a short note" in PS2.clips[0], repr(PS2.clips))

# --- prepare_spoken full ----------------------------------------------------
# Build long spoken prose (no markdown needed if strip=False)
LONG_PROSE = " ".join(f"word{i}" for i in range(40))
PF = tts.prepare_spoken(
    LONG_PROSE,
    spoken_mode="full",
    clip_max_chars=40,
    strip=False,
)
check("full mode spoken_mode field", PF.spoken_mode == "full")
check("full mode multiple clips when over clip_max", len(PF.clips) > 1, repr(PF.clips))
check("full mode chunked flag", PF.chunked is True)
check("full mode not truncated", PF.truncated is False)
check(
    "full mode every char covered",
    " ".join(PF.clips) == LONG_PROSE,
    repr(PF.clips),
)
check(
    "full mode no clip exceeds clip_max",
    all(len(c) <= 40 for c in PF.clips),
    repr([(c, len(c)) for c in PF.clips]),
)

# full mode unbounded (clip_max=0)
PF0 = tts.prepare_spoken(LONG_PROSE, spoken_mode="full", clip_max_chars=0, strip=False)
check("full clip_max=0: single unbounded clip", len(PF0.clips) == 1, repr(PF0.clips))
check("full clip_max=0: not chunked", PF0.chunked is False)
check("full clip_max=0: content identical", PF0.clips[0] == LONG_PROSE, repr(PF0.clips))

# full mode with strip + collapse adjacent refs
PF_STRIP = tts.prepare_spoken(
    "```\na\n```\n```\nb\n```\n```\nc\n``` more prose after",
    spoken_mode="full",
    clip_max_chars=0,
)
check(
    "full+strip collapses adjacent code refs",
    " ".join(PF_STRIP.clips).count("ref. the message for the code") == 1,
    repr(PF_STRIP.clips),
)
check(
    "full+strip keeps trailing prose",
    "more prose after" in " ".join(PF_STRIP.clips),
    repr(PF_STRIP.clips),
)

# empty input
PE = tts.prepare_spoken("")
check("empty input -> zero clips", PE.clips == ())
check("empty input spoken_chars 0", PE.spoken_chars == 0)

# --- prepare_spoken_from_config ---------------------------------------------
cfg_short = {
    "tts": {
        "spoken_mode": "short",
        "spoken_max_chars": 200,
        "voice_code_ref": "CODEREF",
        "voice_link_ref": "LINKREF",
    }
}
PC = tts.prepare_spoken_from_config(
    "see `x` and [t](https://u.example/z) plus more",
    config=cfg_short,
)
check("from_config short mode", PC.spoken_mode == "short")
check("from_config honors voice_code_ref", any("CODEREF" in c for c in PC.clips), repr(PC.clips))
check("from_config honors voice_link_ref", any("LINKREF" in c for c in PC.clips), repr(PC.clips))
check("from_config strips URL", all("example" not in c for c in PC.clips), repr(PC.clips))

cfg_full = {
    "tts": {
        "spoken_mode": "full",
        "hook_voice_max_chars": 30,  # legacy alias
    }
}
long_words = " ".join(f"tok{i}" for i in range(25))
PCF = tts.prepare_spoken_from_config(long_words, config=cfg_full, strip=False)
check(
    "from_config full uses hook_voice_max_chars legacy",
    PCF.chunked is True or len(PCF.clips) >= 1,
    repr(PCF),
)
check(
    "from_config full clips respect legacy max",
    all(len(c) <= 30 for c in PCF.clips),
    repr([(c, len(c)) for c in PCF.clips]),
)

# overrides win over config
PCO = tts.prepare_spoken_from_config(
    "hello world",
    config={"tts": {"spoken_mode": "full"}},
    spoken_mode="short",
    spoken_max_chars=100,
    strip=False,
)
check("kwargs override config spoken_mode", PCO.spoken_mode == "short", repr(PCO))

# --- as_list helper ---------------------------------------------------------
check(
    "SpokenClips.as_list returns list",
    isinstance(PS2.as_list(), list) and PS2.as_list() == list(PS2.clips),
)

LONG_PLAN = "WAVE_PLAN " + ("step " * 400)
VP = tts.plan_voice_send(
    LONG_PLAN,
    tts_mode="text+voice",
    is_hook=False,
    hook_voice=True,
    total_pages=3,
    tts_max_chars=600,
    spoken_mode_cfg="short",
    spoken_max_chars=600,
    clip_max_chars=1500,
)
check("plan_voice_send: multi-page direct eligible", VP.eligible is True, repr(VP))
check("plan_voice_send: multi-page upgrades to full", VP.spoken_mode == "full", repr(VP))

print(f"\n  tts package: {PASS} passed, {FAIL} failed")
if FAIL:
    print("  FAILED:", ", ".join(FAILED))
    sys.exit(1)
sys.exit(0)

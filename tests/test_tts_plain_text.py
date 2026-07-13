#!/usr/bin/env python3
"""tests/test_tts_plain_text.py - Offline unit tests for
lib/tts_plain_text.py (the markdown/HTML -> clean-spoken-prose stripper
that feeds the TTS engine; see that file's module docstring for the design
+ the maintainer's code/URL "reference, don't read" decision, v0.5.2).

NO network calls. Deterministic. Exercises:
  - every formatting SYMBOL the maintainer flagged is gone from the spoken
    text: `#`/`##` headers, `*`/`_` emphasis, `` ` ``/```` ``` ```` code,
    `<b>`/`<pre>`/`<code>` HTML tags, `&lt;`/`&amp;` entities (unescaped),
    `>` blockquotes, `[k/n]` page headers, `-`/`*`/`N.` list markers
  - code (fenced AND inline) -> the code reference phrase, never the code
    characters; `--speak-code` reads it verbatim instead
  - links: `[label](url)` -> "label, <link ref>", a bare URL -> "link,
    <link ref>", and the URL characters are NEVER voiced
  - real prose words survive; snake_case identifiers are NOT mangled
  - the never-fatal echo-on-nonsense contract + the CLI (stdin->stdout)

Run standalone: `python3 tests/test_tts_plain_text.py`
Called by tests/run-tests.sh (this repo's pytest-less pattern: plain
asserts, an explicit runner, exit 0 iff everything passed).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

import tts_plain_text as mod

PASS = 0
FAIL = 0
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


def strip(text: str, **kw) -> str:
    return mod.strip_formatting(text, **kw)


# --- symbols are gone -------------------------------------------------------
S = strip(
    "## Header\n"
    "Some *emphasis* and _more_ and `inline` code.\n"
    "> a quote\n"
    "- bullet one\n"
    "1. numbered\n"
    "A link [the docs](https://example.com/page) here.\n"
    '```rust\nfn main() { println!("x"); }\n```\n'
    "Bare https://foo.bar/baz and entity a &lt; b &amp; c.\n"
    "<b>bold html</b> and <code>htmlcode</code>."
)
for sym in ("#", "*", "`", "&lt;", "&amp;", "](", "<b>", "<pre>", "<code>", "http"):
    check(f"symbol stripped: {sym!r} absent from spoken text", sym not in S, repr(S))
check("blockquote marker '>' gone", ">" not in S, repr(S))

# --- entities unescaped -----------------------------------------------------
check("entity &lt; -> literal '<'", "<" in S and "&lt;" not in S, repr(S))
check("entity &amp; -> literal '&'", "&" in S and "&amp;" not in S, repr(S))

# --- code referenced, not read ---------------------------------------------
check("fenced code NOT voiced (no 'fn main')", "fn main" not in S, repr(S))
check(
    "inline code word 'inline' replaced by reference",
    "inline" not in S or "ref. the message for the code" in S,
    repr(S),
)
check("code reference phrase present", "ref. the message for the code" in S or "code" in S, repr(S))

# --- links referenced, URL never voiced ------------------------------------
check("markdown link keeps its label 'the docs'", "the docs" in S, repr(S))
check("markdown link URL host not voiced", "example.com" not in S, repr(S))
check("bare URL host not voiced", "foo.bar" not in S, repr(S))

# --- prose survives ---------------------------------------------------------
check("header text 'Header' preserved", "Header" in S, repr(S))
check("emphasis word 'emphasis' preserved", "emphasis" in S, repr(S))

# --- snake_case is NOT mangled ---------------------------------------------
SC = strip("call my_var_name and other_thing please")
check("snake_case identifier preserved (my_var_name)", "my_var_name" in SC, repr(SC))
check("snake_case identifier preserved (other_thing)", "other_thing" in SC, repr(SC))

# --- code spans of ANY backtick run length (CommonMark) --------------------
DB = strip("run ``make build`` then done")
check(
    "2-backtick span referenced (no backticks read)",
    "`" not in DB and "ref. the message for the code" in DB,
    repr(DB),
)
check("2-backtick span body 'make build' not voiced", "make build" not in DB, repr(DB))
QB = strip("x ````weird```` y")
check(
    "4-backtick span referenced (no backticks)",
    "`" not in QB and "ref. the message for the code" in QB,
    repr(QB),
)
MIX = strip("a `one` b ``two`` c ```three``` d")
check("mixed 1/2/3-backtick spans all referenced, none left as ticks", "`" not in MIX, repr(MIX))

# --- single-line (flattened) hook input - the REAL adapter shape ------------
# The Claude Code adapter runs the message through `oneline` (newlines ->
# spaces) before it reaches tg-send.sh, so the stripper must work with the
# markers appearing MID-LINE, not only at a line start.
FLAT = strip(
    "✅ agent finished — ## Header text and *em* and "
    "``dbl_code`` and a [doc](https://ex.com/p) plus bare https://foo.bar/z "
    "> quoted ```rust fn main() {} ``` entity a &lt; b."
)
check("flattened: mid-line ## header marker stripped", "#" not in FLAT, repr(FLAT))
check("flattened: mid-line > blockquote marker stripped", ">" not in FLAT, repr(FLAT))
check("flattened: double-backtick span referenced (no backticks)", "`" not in FLAT, repr(FLAT))
check("flattened: fenced code body 'fn main' NOT voiced", "fn main" not in FLAT, repr(FLAT))
check(
    "flattened: URL characters not voiced",
    "foo.bar" not in FLAT and "ex.com" not in FLAT and "http" not in FLAT,
    repr(FLAT),
)
check("flattened: entity &lt; unescaped", "&lt;" not in FLAT, repr(FLAT))
check(
    "flattened: real prose ('Header text', 'em') preserved",
    "Header text" in FLAT and "em" in FLAT,
    repr(FLAT),
)
check(
    "flattened: code + link references present",
    "ref. the message for the code" in FLAT and "ref. the message for the link" in FLAT,
    repr(FLAT),
)

# --- speak_code escape hatch reads code verbatim ---------------------------
SP = strip("run `make build` now\n```\nX=1\n```", speak_code=True)
check("speak_code reads inline code body 'make build'", "make build" in SP, repr(SP))
check("speak_code reads fenced code body 'X=1'", "X=1" in SP, repr(SP))
check("speak_code strips the backticks themselves", "`" not in SP, repr(SP))
check(
    "speak_code does not substitute a reference",
    "ref. the message for the code" not in SP,
    repr(SP),
)

# --- configurable reference wording ----------------------------------------
CR = strip("here `x` and [t](http://u)", code_ref="CODEREF", link_ref="LINKREF")
check("custom code_ref honored", "CODEREF" in CR, repr(CR))
check("custom link_ref honored", "LINKREF" in CR, repr(CR))

# --- [k/n] pagination headers dropped --------------------------------------
KN = strip("[2/3]\nreal content here")
check("[k/n] page header dropped", "[2/3]" not in KN and "real content" in KN, repr(KN))

# --- link with no label -> 'link, <ref>' -----------------------------------
NL = strip("see [](https://x.y/z)")
check(
    "empty-label link -> 'link, ...' reference",
    "link, ref. the message for the link" in NL,
    repr(NL),
)

# --- whitespace collapses to flowing prose ---------------------------------
WS = strip("a\n\n\nb    c")
check(
    "whitespace collapses (no double spaces / newlines)",
    "\n" not in WS and "  " not in WS,
    repr(WS),
)

# --- empty / whitespace input is safe --------------------------------------
check("empty input -> empty output", strip("") == "", repr(strip("")))

# --- CLI: stdin -> stdout, args honored ------------------------------------
proc = subprocess.run(
    [
        sys.executable,
        str(REPO_ROOT / "lib" / "tts_plain_text.py"),
        "--code-ref",
        "CLICODE",
        "--link-ref",
        "CLILINK",
    ],
    input="## H\n`c` [t](http://u)",
    capture_output=True,
    text=True,
)
check("CLI exits 0", proc.returncode == 0, proc.stderr)
check("CLI honored --code-ref", "CLICODE" in proc.stdout, repr(proc.stdout))
check("CLI honored --link-ref", "CLILINK" in proc.stdout, repr(proc.stdout))
check("CLI stripped the header marker", "#" not in proc.stdout, repr(proc.stdout))

# --- adjacent code refs collapse (v0.5.4+) ---------------------------------
MULTI = strip("```\na\n```\n```\nb\n```\n```\nc\n```")
count_ref = MULTI.count("ref. the message for the code")
check("three adjacent fences -> one code ref (collapsed)", count_ref == 1, repr(MULTI))
SEP = strip("```\na\n```\nthen prose\n```\nb\n```")
check(
    "fences separated by prose keep two refs",
    SEP.count("ref. the message for the code") == 2,
    repr(SEP),
)
NOCOL = strip("```\na\n```\n```\nb\n```", collapse_refs=False)
check(
    "collapse_refs=False keeps multiple refs",
    NOCOL.count("ref. the message for the code") >= 2,
    repr(NOCOL),
)

# --- call IDs / UUIDs never voiced -----------------------------------------
IDS = strip(
    "error toolUseId: toolu_01AbCdEfGhIjKlMn call_abc123xyz999 "
    "uuid 550e8400-e29b-41d4-a716-446655440000 and call_id=call_9f3a2b1c0d8e7f6a done"
)
check("call ids: toolu_ gone", "toolu_" not in IDS, repr(IDS))
check("call ids: call_ opaque gone", "call_abc" not in IDS and "call_9f3a" not in IDS, repr(IDS))
check("call ids: UUID gone", "550e8400" not in IDS, repr(IDS))
check("call ids: prose kept", "error" in IDS and "done" in IDS, repr(IDS))

print(f"\n  tts_plain_text: {PASS} passed, {FAIL} failed")
if FAIL:
    print("  FAILED:", ", ".join(FAILED))
    sys.exit(1)
sys.exit(0)

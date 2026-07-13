#!/usr/bin/env python3
"""tts_plain_text.py - strip markdown/HTML to clean spoken prose for TTS.

Reads the message text on stdin, writes a plain-text transcript on stdout
that a TTS engine (piper/espeak-ng) can read as WORDS - never the
formatting SYMBOLS (`#`/`##` headers, `*`/`_` emphasis, `` ` ``/```` ``` ````
code, `<b>`/`<pre>`/`<code>` HTML tags, `&lt;` entities, `>` blockquotes,
`[k/n]` page headers, `-`/`*`/`N.` list markers). Called by lib/tts.sh's
`_tts_plain_text` (via `python3`, stdlib-only - the same zero-dependency
convention as lib/toml_to_json.py / lib/code_highlight.py), which then
feeds the result to piper/espeak. The SENT text message keeps its FULL
formatting; only this spoken copy is stripped (see lib/tts.sh /
tg-send.sh) - never change what is sent as text.

Code and links are, by the maintainer's explicit choice (v0.5.2), NOT read
aloud - reading code characters or spelling out a URL (`h-t-t-p-s-colon-
slash-slash...`) is noise. Each is replaced with a short spoken REFERENCE
back to the text message, so the listener knows to look at the chat bubble:
  - a code span of ANY backtick run length -> the `--code-ref` phrase
    (default "code, see the text message"): `x` (1 backtick), ``x`` (2 -
    the shape the maintainer's messages use), ```lang ... ``` (3+, fenced),
    single- or multi-line, all detected via the CommonMark rule (a run of
    N backticks opens, the next run of N backticks closes).
  - a Markdown link `[label](url)`  -> "<label>, <link-ref>" (the link
    TEXT is kept, the URL is dropped); a bare URL -> "link, <link-ref>"
    (`--link-ref` default "see the text message"). The URL characters are
    never voiced either way.
`--speak-code` is an opt-in escape hatch that reads code content verbatim
instead of referencing it (default off - reference).

HTML entities are unescaped (`&lt;`->`<`, `&amp;`->`&`, ...) so the voice
says the character, not "ampersand l t".

Never-fatal (lib/tts.sh's skip-graceful contract): on ANY error this
echoes stdin unchanged - the voice still speaks, just unstripped - rather
than dropping the voice note. Deterministic, offline, no third-party deps.

Run standalone: `printf '## Hi *there*' | python3 lib/tts_plain_text.py`
"""
from __future__ import annotations

import argparse
import html
import re
import sys


def collapse_adjacent_refs(text: str, *phrases: str) -> str:
    """Collapse consecutive identical reference phrases (code/link refs)
    separated only by whitespace or light punctuation into one utterance,
    so three adjacent code blocks do not become
    'ref… ref… ref…' back-to-back."""
    if not text or not phrases:
        return text
    # Longest phrases first so a longer ref isn't partially matched.
    ordered = sorted({p for p in phrases if p}, key=len, reverse=True)
    if not ordered:
        return text
    # Build alternation of escaped phrases.
    alt = "|".join(re.escape(p) for p in ordered)
    # phrase (,|.)? + whitespace + same phrase  -> keep one phrase
    pattern = re.compile(
        rf"(?P<p>{alt})(?:\s*[,;.…]?\s+)(?P=p)(?:\s*[,;.…]?\s*(?P=p))*",
        re.IGNORECASE,
    )
    prev = None
    out = text
    # Iterate until stable (handles A A A A chains).
    while prev != out:
        prev = out
        out = pattern.sub(lambda m: m.group("p"), out)
    # Also collapse "label, <link_ref>" repeated with only punctuation between
    # when the whole "…, link_ref" form repeats.
    return re.sub(r"[ \t]+", " ", out).strip()


def strip_formatting(
    text: str,
    code_ref: str = "ref. the message for the code",
    link_ref: str = "ref. the message for the link",
    speak_code: bool = False,
    collapse_refs: bool = True,
) -> str:
    """Return `text` as clean spoken prose. Pure function, never raises for
    normal string input (the CLI wrapper adds the belt-and-suspenders
    echo-on-error guard on top)."""

    # NB: the live TTS input is often a SINGLE flattened line (the Claude
    # Code adapter runs the hook message through `oneline` before it reaches
    # tg-send.sh, so newlines are already spaces). Every rule below therefore
    # works WITHOUT relying on line structure - code fences, headers and
    # blockquote markers are matched inline, not only at a line start.

    # 1. Multi-line TILDE fences (~~~), for a direct/multi-line send. Backtick
    #    fences of any length are handled by the general code-span rule (3).
    def _fence(m: "re.Match[str]") -> str:
        return m.group("body") if speak_code else code_ref

    text = re.sub(
        r"(?s)(?P<f>~{3,})[^\n]*\n(?P<body>.*?)(?P=f)", _fence, text
    )
    text = re.sub(r"~{3,}", "" if speak_code else code_ref, text)

    # 2. HTML <pre>...</pre> code boxes (what lib/format.sh emits), multi-
    #    line. Defensive: the live TTS input is the raw markdown, but a
    #    caller may hand us the post-format HTML instead - handle both.
    def _pre(m: "re.Match[str]") -> str:
        if speak_code:
            return re.sub(r"<[^>]+>", "", m.group("body"))
        return code_ref

    text = re.sub(r"(?is)<pre\b[^>]*>(?P<body>.*?)</pre>", _pre, text)

    # 2b. HTML inline <code>...</code> -> reference (or bare inner word).
    def _htmlcode(m: "re.Match[str]") -> str:
        return m.group("body") if speak_code else code_ref

    text = re.sub(r"(?is)<code\b[^>]*>(?P<body>.*?)</code>", _htmlcode, text)

    # 3. Backtick code spans of ANY run length (CommonMark: a run of N
    #    backticks opens the span and the next run of N backticks closes it -
    #    N may be 1, 2, 3, 4...). This single matcher covers `x` (1), ``x``
    #    (2, the shape the maintainer's messages actually use), and
    #    ```lang ... ``` (3+, fenced) alike, single- OR multi-line - so a
    #    2-backtick span is never left to be read as "backtick backtick".
    #    `.+?` is lazy so adjacent spans don't merge; `\1` forces an
    #    equal-length closing run.
    def _span(m: "re.Match[str]") -> str:
        return m.group(2) if speak_code else code_ref

    text = re.sub(r"(?s)(`+)(.+?)\1", _span, text)
    # Any leftover UNBALANCED backtick run -> removed (never voiced as ticks).
    text = re.sub(r"`+", "", text)

    # 4. Markdown links [label](url) -> "label, <link_ref>" (NEVER the url).
    def _link(m: "re.Match[str]") -> str:
        label = m.group(1).strip()
        return f"{label}, {link_ref}" if label else f"link, {link_ref}"

    text = re.sub(r"\[([^\]]*)\]\((?:[^)]*)\)", _link, text)

    # 5. Bare URLs -> "link, <link_ref>" (never spell the URL out).
    text = re.sub(r"(?i)\b(?:https?://|www\.)\S+", f"link, {link_ref}", text)

    # 6. Any remaining HTML tags -> removed, inner text kept (<b>, <i>, ...).
    text = re.sub(r"<[^>]+>", "", text)

    # 7. Unescape HTML entities so the voice says the char, not "&lt;".
    text = html.unescape(text)

    # 8. Markdown line-markers - matched INLINE (flattened-input-safe), at a
    #    string/line start OR after whitespace, never mid-word:
    text = re.sub(r"(?:(?<=\s)|^)#{1,6}[ \t]+", "", text)       # ## headers
    text = re.sub(r"(?:(?<=\s)|^)>{1,}[ \t]+", "", text)         # > blockquotes
    text = re.sub(r"(?m)^[ \t]*[-+*][ \t]+", "", text)          # leading list bullets
    text = re.sub(r"(?m)^[ \t]*\d+[.)][ \t]+", "", text)       # leading ordered list
    text = re.sub(r"\[\d+/\d+\][ \t]*", "", text)               # [k/n] page header, anywhere

    # 9. Emphasis markers *word* / _word_ -> word. Boundary-guarded so a
    #    snake_case identifier (my_var_name) is left intact - the classic
    #    markdown-in-prose false positive lib/format.sh also guards against.
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", text)
    text = re.sub(r"(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])", r"\1", text)
    # Any leftover asterisks (stray bullets/bold runs) -> dropped.
    text = text.replace("*", "")

    # 10. Collapse whitespace so the read flows as continuous prose.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip()

    # 11. Collapse adjacent identical code/link reference phrases so a run of
    #     fences does not voice "see the code" three times in a row.
    if collapse_refs and not speak_code:
        text = collapse_adjacent_refs(text, code_ref, link_ref, f"link, {link_ref}")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True, description=__doc__)
    parser.add_argument("--code-ref", default="ref. the message for the code")
    parser.add_argument("--link-ref", default="ref. the message for the link")
    parser.add_argument("--speak-code", action="store_true")
    parser.add_argument(
        "--no-collapse-refs",
        action="store_true",
        help="Do not collapse consecutive identical code/link reference phrases",
    )
    args = parser.parse_args()

    raw = ""
    try:
        raw = sys.stdin.read()
        out = strip_formatting(
            raw,
            code_ref=args.code_ref,
            link_ref=args.link_ref,
            speak_code=args.speak_code,
            collapse_refs=not args.no_collapse_refs,
        )
    except Exception:
        # Never-fatal: echo stdin unchanged so the voice still speaks.
        try:
            sys.stdout.write(raw)
        except Exception:
            pass
        return 0
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

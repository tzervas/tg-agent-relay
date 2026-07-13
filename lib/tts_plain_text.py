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
  - a fenced OR inline code span -> the `--code-ref` phrase
    (default "code, see the text message").
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


def strip_formatting(
    text: str,
    code_ref: str = "code, see the text message",
    link_ref: str = "see the text message",
    speak_code: bool = False,
) -> str:
    """Return `text` as clean spoken prose. Pure function, never raises for
    normal string input (the CLI wrapper adds the belt-and-suspenders
    echo-on-error guard on top)."""

    # 1. Fenced code blocks (``` or ~~~), possibly multi-line. The closing
    #    fence must repeat the SAME marker run (backreference). speak_code
    #    keeps the body; otherwise the whole block becomes one reference.
    def _fence(m: "re.Match[str]") -> str:
        return m.group("body") if speak_code else code_ref

    text = re.sub(
        r"(?s)(?P<f>`{3,}|~{3,})[^\n]*\n(?P<body>.*?)(?P=f)",
        _fence,
        text,
    )
    # Any dangling fence marker (an unclosed block) -> reference / removed,
    # so a stray ``` is never spelled out as "backtick backtick backtick".
    text = re.sub(r"`{3,}|~{3,}", "" if speak_code else code_ref, text)

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

    # 3. Markdown inline code `code` -> reference (or the bare word).
    text = re.sub(
        r"`([^`\n]+)`",
        (lambda m: m.group(1)) if speak_code else (lambda m: code_ref),
        text,
    )

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

    # 8. Line-start markers (headers / blockquotes / list bullets / [k/n]).
    text = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]*", "", text)          # ATX headers
    text = re.sub(r"(?m)^[ \t]*(?:>[ \t]*)+", "", text)          # blockquotes
    text = re.sub(r"(?m)^[ \t]*[-+*][ \t]+", "", text)           # unordered list
    text = re.sub(r"(?m)^[ \t]*\d+[.)][ \t]+", "", text)        # ordered list
    text = re.sub(r"(?m)^[ \t]*\[\d+/\d+\][ \t]*$", "", text)   # [k/n] page-header line
    text = re.sub(r"(?m)^[ \t]*\[\d+/\d+\][ \t]*", "", text)    # [k/n] leading prefix

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
    return text.strip()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True, description=__doc__)
    parser.add_argument("--code-ref", default="code, see the text message")
    parser.add_argument("--link-ref", default="see the text message")
    parser.add_argument("--speak-code", action="store_true")
    args = parser.parse_args()

    raw = ""
    try:
        raw = sys.stdin.read()
        out = strip_formatting(
            raw,
            code_ref=args.code_ref,
            link_ref=args.link_ref,
            speak_code=args.speak_code,
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

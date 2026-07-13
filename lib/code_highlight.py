#!/usr/bin/env python3
"""lib/code_highlight.py - host-highlighted, self-contained HTML document
renderer for a fenced code block.

Design rationale (see docs/USAGE.md's "Syntax-highlighted code images"
section / the [code_highlight] header in relay.toml.example for the full
writeup): Telegram message TEXT supports no color at all - a fixed HTML
entity set, and `<pre>`/`<code>` cannot even NEST `<b>`/`<i>` around
individual tokens (see lib/format.sh's header) - so true colored syntax
highlighting INSIDE a chat bubble is structurally impossible as text.
`<pre><code class="language-X">` (lib/format.sh's existing, ALWAYS-ON
v0.3.0 code box - unchanged, still the default, still what every fenced
block renders as in the message itself) only lights up on a Telegram
CLIENT that ships its own syntax highlighter, and even then it's plain
monochrome-per-message-theme, never truly colored per-token.

This module renders the HOST-SIDE highlighting instead: pygments -> a
single self-contained HTML file (inline CSS, no external stylesheet -
`HtmlFormatter(full=True, noclasses=True)`), sent via Telegram's
`sendDocument` (lib/code_highlight.sh drives this script from the
outbound send path and is the only caller). Opened in the phone's browser,
it renders with REAL per-token colors on any device, no local highlighter
needed - and unlike an image, the code stays selectable/copyable inside
the document itself.

OPTIONAL dependency, skip-graceful exactly like lib/tts.sh's piper/
espeak-ng and lib/dashboard_render.py's matplotlib: if `pygments` is not
installed (Pillow is NOT needed for this - `HtmlFormatter` is pure text
generation, no image/font library involved at all), this prints a SKIP:
line and the caller relies on the ALREADY-SENT v0.3.0 `<pre><code>` text
box - a code block is never dropped, never crashes the send path.

Contract (lib/code_highlight.sh is the only caller):
    code_highlight.py <lang> <out_html_path> [--theme=NAME] [--line-numbers]
                       [--max-lines=N]
    <code to highlight> on stdin (utf-8)
Prints EXACTLY one line to stdout:
    DOC:<out_html_path>    - HTML file written successfully; caller sends
                              it via Telegram's sendDocument.
    SKIP:<reason>          - graceful skip (dependency missing, over
                              max_lines, empty input, or a render error) -
                              caller just doesn't send a document (the
                              v0.3.0 text code box already sent in the
                              main message is unaffected either way).
                              NEVER a crash/traceback/nonzero exit for a
                              rendering CONDITION - the only nonzero exit
                              is a genuine CLI-usage bug (too few
                              arguments), a caller bug rather than a
                              runtime/data condition - the same split
                              lib/dashboard_render.py uses.

Native Mycelium lexer: pygments ships no lexer for Mycelium (an
in-development language - see docs/Mycelium_Project_Foundation.md in the
mycelium repo), so this file defines a minimal MyceliumLexer (a pygments
RegexLexer) covering the keywords/tokens actually in use: nodule / phylum /
colony / hypha / fn / swap / fuse / let / match / if / else / return, the
Value/Result/Option/Dense/Ternary/Binary/VSA core types, `//` line comments
(the `// nodule:` header specially tagged), string/char literals, and
integer/float/hex numbers. It registers under BOTH `myc` and `mycelium`
fence tags - the same two aliases lib/format.sh already treats as
first-class (see its `_fmt_known_lang`). This is a `Declared`,
best-effort lexer (a lexical approximation, not a validated grammar - see
CLAUDE.md's transparency rule in the mycelium repo): it highlights the
tokens it recognizes and leaves the rest as plain text, exactly like any
other pygments lexer degrades on unfamiliar syntax - never a crash, never
mis-highlighted-as-the-wrong-language.

Any OTHER fence tag is resolved via pygments' own `get_lexer_by_name`
(hundreds of languages - well past lib/format.sh's small `_fmt_known_lang`
CSS-class allowlist; that allowlist only gates whether a `language-X` CSS
class is emitted on the inline text box, it is NOT a language whitelist
for this renderer). An unknown/unrecognized tag - or no tag at all - falls
back to pygments' own plain-text lexer: the document still renders
cleanly, just without color, never a crash.
"""

from __future__ import annotations

import html
import sys
from pathlib import Path


def _mycelium_lexer_cls():
    """Builds MyceliumLexer lazily (only once pygments is confirmed
    importable) - see this file's module docstring for the token coverage
    and its honesty tag (Declared, lexical approximation)."""
    from pygments.lexer import RegexLexer, words
    from pygments.token import (
        Comment,
        Keyword,
        Name,
        Number,
        Operator,
        Punctuation,
        String,
        Whitespace,
    )

    class MyceliumLexer(RegexLexer):
        """Minimal, best-effort lexer for Mycelium (`.myc`) source - see
        this module's docstring for scope/limitations (Declared, not a
        validated grammar)."""

        name = "Mycelium"
        aliases = ["myc", "mycelium"]
        filenames = ["*.myc"]
        mimetypes = ["text/x-mycelium"]

        # Active/reserved keywords per the lang lexicon (nodule is the only
        # ACTIVE keyword today; phylum/colony/hypha/fuse/swap are
        # reserved-not-yet-active or ratified-not-yet-lexed - highlighted
        # the same either way, since this is a lexer, not a validator).
        _keywords = (
            "nodule",
            "phylum",
            "colony",
            "hypha",
            "fn",
            "swap",
            "fuse",
            "let",
            "mut",
            "const",
            "match",
            "if",
            "else",
            "return",
            "for",
            "while",
            "loop",
            "break",
            "continue",
            "struct",
            "enum",
            "trait",
            "impl",
            "use",
            "pub",
            "as",
            "in",
            "true",
            "false",
            "self",
        )
        _types = ("Value", "Result", "Option", "Dense", "Ternary", "Binary", "VSA")

        tokens = {
            "root": [
                (r"//\s*nodule:.*$", Comment.Special),
                (r"//.*$", Comment.Single),
                (r"/\*", Comment.Multiline, "comment"),
                (r'"(\\.|[^"\\])*"', String.Double),
                (r"'(\\.|[^'\\])*'", String.Single),
                (r"\b\d+\.\d+([eE][+-]?\d+)?\b", Number.Float),
                (r"\b0x[0-9a-fA-F]+\b", Number.Hex),
                (r"\b\d+\b", Number.Integer),
                (words(_keywords, suffix=r"\b"), Keyword),
                (words(_types, suffix=r"\b"), Keyword.Type),
                (r"\b[A-Z][A-Za-z0-9_]*\b", Name.Class),
                (r"[a-z_][A-Za-z0-9_]*(?=\()", Name.Function),
                (r"[a-z_][A-Za-z0-9_]*", Name),
                (r"[+\-*/%=<>!&|^~]+", Operator),
                (r"[{}()\[\];,.:]", Punctuation),
                (r"\s+", Whitespace),
                # Never-silent: any other single byte is still CONSUMED
                # (advances the lexer), never dropped or left to loop -
                # pygments would otherwise raise on an un-matched byte.
                (r".", Name),
            ],
            "comment": [
                (r"[^*/]+", Comment.Multiline),
                (r"/\*", Comment.Multiline, "#push"),
                (r"\*/", Comment.Multiline, "#pop"),
                (r"[*/]", Comment.Multiline),
            ],
        }

    return MyceliumLexer


def _get_lexer(lang: str):
    """<lang> -> a pygments lexer instance. `myc`/`mycelium` -> this file's
    own MyceliumLexer; empty/unrecognized -> pygments' TextLexer (still a
    clean rendered document, no crash); anything else -> pygments'
    `get_lexer_by_name`, which itself already degrades unfamiliar SYNTAX to
    plain tokens (a lexer content mismatch is not the same problem as an
    unknown language TAG - both are handled, neither ever raises here)."""
    from pygments.lexers.special import TextLexer

    tag = (lang or "").strip().lower()
    if tag in ("myc", "mycelium"):
        return _mycelium_lexer_cls()(stripnl=False)
    if not tag:
        return TextLexer(stripnl=False)
    try:
        from pygments.lexers import get_lexer_by_name
        from pygments.util import ClassNotFound

        return get_lexer_by_name(tag, stripnl=False)
    except ClassNotFound:
        return TextLexer(stripnl=False)


def render_code_html(
    code: str,
    lang: str,
    out_path: str,
    *,
    theme: str = "monokai",
    line_numbers: bool = False,
    max_lines: int = 60,
) -> tuple[bool, str]:
    """The library entry point (also used directly by tests - no subprocess
    needed). Returns (ok, reason) and NEVER raises: any import/render/write
    failure is caught and reported as (False, reason) - the same
    "never raise, degrade instead" contract lib/dashboard_render.py uses
    for its matplotlib render. Writes a SELF-CONTAINED HTML document (all
    CSS inlined via `noclasses=True` - no external stylesheet, no network
    fetch needed to view it) to <out_path>.
    """
    if not code.strip():
        return False, "empty code block"

    line_count = code.count("\n") + 1
    if line_count > max_lines:
        return False, f"exceeds max_lines ({line_count} > {max_lines})"

    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
    except ImportError as exc:
        return False, f"pygments unavailable ({exc})"

    try:
        lexer = _get_lexer(lang)
        # `lang` crosses a trust boundary here: today lib/code_highlight.sh's
        # fence regex constrains it to [A-Za-z0-9_+.-] before this module
        # ever sees it, but HtmlFormatter(title=...) does NOT html-escape
        # its `title` (unlike the highlighted code content, which pygments
        # already escapes) - it lands verbatim in the generated document's
        # <title>/<h2>. This module must not rely on the caller's
        # constraint holding forever, so escape defensively here too
        # (defense-in-depth, not a currently-reachable exploit).
        title = html.escape(f"{lang or 'code'} snippet")
        formatter = HtmlFormatter(
            full=True,
            style=theme,
            noclasses=True,
            linenos="table" if line_numbers else False,
            title=title,
            encoding="utf-8",
        )
        # An explicit `encoding` makes HtmlFormatter.format() return utf-8
        # BYTES (also fixing the document's own <meta charset> from a bare
        # "None" to a real value) - written directly, no text-mode guessing.
        html_bytes = highlight(code, lexer, formatter)
    except Exception as exc:
        return False, f"render failed: {exc}"

    if not html_bytes:
        return False, "renderer produced no document content"

    try:
        Path(out_path).write_bytes(html_bytes)
    except OSError as exc:
        return False, f"write failed: {exc}"

    if not (Path(out_path).is_file() and Path(out_path).stat().st_size > 0):
        return False, "empty output file"
    return True, ""


def _parse_args(argv: list[str]) -> dict | None:
    """Manual flag parsing (matches this repo's style - see
    lib/dashboard_render.py's plain argv indexing rather than an argparse
    dependency). `--flag=value` form only (the one shape
    lib/code_highlight.sh ever emits - keeps this parser unambiguous).
    Returns None on a genuine CLI-usage error (fewer than the two required
    positional args)."""
    positional: list[str] = []
    opts: dict = {"theme": "monokai", "line_numbers": False, "max_lines": 60}
    for a in argv[1:]:
        if a == "--line-numbers":
            opts["line_numbers"] = True
        elif a.startswith("--theme="):
            opts["theme"] = a[len("--theme=") :]
        elif a.startswith("--max-lines="):
            try:
                opts["max_lines"] = int(a[len("--max-lines=") :])
            except ValueError:
                pass
        elif not a.startswith("--"):
            positional.append(a)

    if len(positional) < 2:
        return None
    opts["lang"] = positional[0]
    opts["out_path"] = positional[1]
    return opts


def main(argv: list[str]) -> int:
    opts = _parse_args(argv)
    if opts is None:
        print(
            "usage: code_highlight.py <lang> <out_html_path> [--theme=NAME] "
            "[--line-numbers] [--max-lines=N]  (code on stdin)",
            file=sys.stderr,
        )
        return 2

    code = sys.stdin.read()
    ok, reason = render_code_html(
        code,
        opts["lang"],
        opts["out_path"],
        theme=opts["theme"],
        line_numbers=opts["line_numbers"],
        max_lines=opts["max_lines"],
    )
    if ok:
        print(f"DOC:{opts['out_path']}")
    else:
        print(f"SKIP:{reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

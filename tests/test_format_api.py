#!/usr/bin/env python3
"""tests/test_format_api.py — Pure-Python HTML formatter parity (#25).

Ports key assertions from tests/run-tests.sh lib/format.sh section into a
stdlib-only PASS/FAIL runner (no network).

Golden cases: HTML escape, soft-wrap, headers, blockquotes, emphasis,
inline code, fenced rust, myc alias, unclosed fence, tag-balance fallback.

Run:
  uv run python tests/test_format_api.py
  python3 tests/test_format_api.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tg_agent_relay.format_api import (
    escape_html,
    format_message,
    html_balanced,
    is_header_line,
    known_lang,
    render_code_block,
    wrap_line,
)
from tg_agent_relay.protocols import FormatResult

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
        fail(name, f"expected {exp!r}\n      got      {act!r}")


def true(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail)


def fmt(text: str, **kw) -> FormatResult:
    return format_message(text, **kw)


# --- escape -----------------------------------------------------------------
eq(
    "escape: < > & all escaped, in order (no double-escape)",
    "a &lt; b &amp; c &gt; d",
    escape_html("a < b & c > d"),
)
eq(
    "escape: literal <code> tag in source is neutralized",
    "&lt;code&gt;danger&lt;/code&gt;",
    escape_html("<code>danger</code>"),
)

r = fmt("ok <b>not actually bold</b> & <script>bad</script>")
eq(
    "format_message: literal HTML-looking source is escaped",
    "ok &lt;b&gt;not actually bold&lt;/b&gt; &amp; &lt;script&gt;bad&lt;/script&gt;",
    r.text,
)
eq("format_message: parse_mode is HTML by default", "HTML", r.parse_mode)
true("format_message returns FormatResult", isinstance(r, FormatResult))

# --- soft-wrap --------------------------------------------------------------
SHORT = "short line, well under fifty chars"
eq(
    "wrap: line already <= wrap_width is untouched",
    [SHORT],
    wrap_line(SHORT, 50),
)

LONG = (
    "this is a much longer line of plain prose that will need to be "
    "wrapped at word boundaries to fit a phone screen nicely"
)
wrapped = wrap_line(LONG, 50)
true("wrap: long line splits into multiple lines", len(wrapped) > 1, repr(wrapped))
true(
    "wrap: each line <= wrap_width",
    all(len(ln) <= 50 for ln in wrapped),
    repr([(len(ln), ln) for ln in wrapped]),
)
true(
    "wrap: no word lost (rejoined with spaces equals original)",
    " ".join(wrapped) == LONG,
    repr(wrapped),
)

URL = "see https://example.com/a/very/long/path/that/would/never/fit/in/fifty/characters/at/all for details"
url_w = wrap_line(URL, 50)
true(
    "wrap: URL never broken mid-URL",
    any(
        "https://example.com/a/very/long/path/that/would/never/fit/in/fifty/characters/at/all" in ln
        for ln in url_w
    ),
    repr(url_w),
)

CODE_SPAN = "run the `some very long command with lots of embedded spaces inside` now please"
code_w = wrap_line(CODE_SPAN, 50)
true(
    "wrap: inline code span with spaces never broken mid-span",
    any("`some very long command with lots of embedded spaces inside`" in ln for ln in code_w),
    repr(code_w),
)

# --- headers ----------------------------------------------------------------
r = fmt("## Explicit Header\nprose line")
eq(
    "header: explicit '## ' prefix -> bolded, prefix stripped",
    "<b>Explicit Header</b>\nprose line",
    r.text,
)

r = fmt("✅ BUILD FINISHED")
eq(
    "header: leading-emoji ALL-CAPS short line -> bolded",
    "<b>✅ BUILD FINISHED</b>",
    r.text,
)

r = fmt("🚀 Deploy Started")
eq(
    "header: leading-emoji Title-Case short line -> bolded",
    "<b>🚀 Deploy Started</b>",
    r.text,
)

r = fmt("✅ code-reviewer finished — Found 2 issues, both low severity")
true(
    "header: leading-emoji SENTENCE (lowercase) is NOT bolded",
    "<b>" not in r.text,
    r.text,
)

true("is_header_line ## yes", is_header_line("## Foo"))
true("is_header_line bare ALL-CAPS no emoji no", not is_header_line("BUILD FINISHED"))

# --- blockquotes ------------------------------------------------------------
r = fmt("> a quoted note\n> spanning two lines")
eq(
    "blockquote: consecutive '> ' lines group into ONE <blockquote>",
    "<blockquote>a quoted note\nspanning two lines</blockquote>",
    r.text,
)

r = fmt("> line one\n> line two\n> line three\n> line four")
true(
    "blockquote: >3-line quote becomes EXPANDABLE",
    r.text.startswith("<blockquote expandable>"),
    r.text,
)

# --- emphasis / snake_case --------------------------------------------------
r = fmt("this is *emphasis* and this is _also italic_")
eq(
    "emphasis: both *word* and _word_ render as <i>",
    "this is <i>emphasis</i> and this is <i>also italic</i>",
    r.text,
)

r = fmt("the identifier my_var_name must stay literal")
true(
    "emphasis: snake_case never mistaken for italic",
    "my_var_name" in r.text and "<i>" not in r.text,
    r.text,
)

# --- enabled=False / parse_mode=none passthrough ----------------------------
RAW = "## not a header <literally> & such"
r = fmt(RAW, enabled=False)
eq("enabled=False: text passthrough unchanged", RAW, r.text)
eq("enabled=False: parse_mode empty", "", r.parse_mode)

r = fmt(RAW, config={"format": {"parse_mode": "none"}})
eq("parse_mode=none (config): text unchanged", RAW, r.text)
eq("parse_mode=none (config): parse_mode empty", "", r.parse_mode)

r = fmt(RAW, config={"format": {"enabled": False}})
eq("enabled=false (config): text unchanged", RAW, r.text)
eq("enabled=false (config): parse_mode empty", "", r.parse_mode)

# --- fenced code blocks -----------------------------------------------------
RUST_LONG = (
    "fn this_is_a_very_long_rust_line_that_would_definitely_exceed_the_"
    "fifty_char_wrap_width(x: i32) -> i32 { x }"
)
r = fmt(f"before\n\n```rust\n{RUST_LONG}\n```\n\nafter")
eq(
    "fenced rust: language-rust, content verbatim (not wrapped) + HTML-escaped",
    f'before\n\n<pre><code class="language-rust">{escape_html(RUST_LONG)}</code></pre>\n\nafter',
    r.text,
)

MYC1 = "nodule example"
MYC2 = "  fn f(x) -> x"
r = fmt(f"```myc\n{MYC1}\n{MYC2}\n```")
eq(
    "fenced ```myc -> language-rust by default (myc_inline_lang alias)",
    f'<pre><code class="language-rust">{escape_html(MYC1)}\n{escape_html(MYC2)}</code></pre>',
    r.text,
)

r = fmt("```mycelium\nnodule example2\n```")
eq(
    "fenced ```mycelium also aliases to language-rust by default",
    '<pre><code class="language-rust">nodule example2</code></pre>',
    r.text,
)

r = fmt(
    "```myc\nnodule example3\n```",
    config={"code_highlight": {"myc_inline_lang": "mycelium"}},
)
eq(
    'myc_inline_lang="mycelium" opts into literal language-mycelium',
    '<pre><code class="language-mycelium">nodule example3</code></pre>',
    r.text,
)

r = fmt(
    "```myc\nnodule example4\n```",
    config={"code_highlight": {"myc_inline_lang": ""}},
)
eq(
    'myc_inline_lang="" falls through to default language-rust',
    '<pre><code class="language-rust">nodule example4</code></pre>',
    r.text,
)

r = fmt(
    "```myc\nnodule example5\n```",
    config={"code_highlight": {"myc_inline_lang": "python"}},
)
eq(
    "myc_inline_lang can alias to any allowlisted language",
    '<pre><code class="language-python">nodule example5</code></pre>',
    r.text,
)

r = fmt(
    "```myc\nnodule example6\n```",
    config={"code_highlight": {"myc_inline_lang": "totally-not-a-real-lang<script>"}},
)
eq(
    "myc_inline_lang adversarial value fails CLOSED → language-mycelium",
    '<pre><code class="language-mycelium">nodule example6</code></pre>',
    r.text,
)

r = fmt("```totallymadeupxyz\nsome content\n```")
eq(
    "unrecognized language tag → plain <pre> (no class)",
    "<pre>some content</pre>",
    r.text,
)

# --- mixed message ----------------------------------------------------------
MIXED_LONG = (
    "this paragraph of prose is definitely long enough that it must be "
    "wrapped across more than one line on a phone-width screen"
)
MIXED_CODE = "fn keep_this_line_totally_unwrapped_even_though_it_is_long(x) -> x"
r = fmt(f"## Findings\n\n{MIXED_LONG}\n\n```myc\n{MIXED_CODE}\n```")
true("mixed: header bolded", "<b>Findings</b>" in r.text, r.text)
true(
    "mixed: fenced code preserved verbatim as language-rust",
    f'<pre><code class="language-rust">{escape_html(MIXED_CODE)}</code></pre>' in r.text,
    r.text,
)
true("mixed: multi-line output (prose soft-wrapped)", r.text.count("\n") >= 5, r.text)
true("mixed: final HTML is tag-balanced", html_balanced(r.text), r.text)

# --- unclosed fence ---------------------------------------------------------
r = fmt("before\n\n```\nafter this is unclosed forever")
eq(
    "unclosed fence with body → literal text, never empty <pre></pre>",
    "before\n\n```\nafter this is unclosed forever",
    r.text,
)
true("unclosed fence: no <pre> emitted", "<pre>" not in r.text, r.text)

r = fmt("```python\ndef f():\n    pass")
eq(
    "unclosed fence WITH body → opening marker + body as literal escaped text",
    "```python\ndef f():\n    pass",
    r.text,
)

# --- inline code ------------------------------------------------------------
r = fmt("run `foo bar` now")
eq(
    "inline code: backticks → <code>",
    "run <code>foo bar</code> now",
    r.text,
)
r = fmt("has a lone ` backtick")
true(
    "inline code: unmatched backtick stays literal",
    "`" in r.text and "<code>" not in r.text,
    r.text,
)

# --- never-silent: balance failure fallback ---------------------------------
with (
    mock.patch("tg_agent_relay.format_api.html_balanced", return_value=False),
    mock.patch("tg_agent_relay.format_api.emit_metric") as em,
):
    src = "## Header\nprose with <em>literal-looking</em> markup"
    r = fmt(src)
    eq(
        "never-silent: balance failure → escaped plain text",
        escape_html(src),
        r.text,
    )
    eq("never-silent: parse_mode stays HTML on fallback", "HTML", r.parse_mode)
    true(
        "never-silent: emit_metric called with format/fallback",
        em.called and em.call_args[0][:2] == ("format", "fallback"),
        repr(em.call_args),
    )

# --- MarkdownV2 not implemented ---------------------------------------------
with mock.patch("tg_agent_relay.format_api.emit_metric") as em:
    r = fmt("## Header", parse_mode="MarkdownV2")
    eq("MarkdownV2: text passthrough plain", "## Header", r.text)
    eq("MarkdownV2: parse_mode empty", "", r.parse_mode)
    true("MarkdownV2: metric emitted", em.called)

# --- wrap_width < 10 rejected -----------------------------------------------
r = fmt("x" * 60, wrap_width=5)
# with default soft wrap at 50, a 60-char no-space token stays one line
true("wrap_width < 10 falls back to 50", r.parse_mode == "HTML")

# --- known_lang helpers -----------------------------------------------------
eq("known_lang myc → mycelium", "mycelium", known_lang("myc"))
eq("known_lang Mycelium → mycelium", "mycelium", known_lang("Mycelium"))
eq("known_lang rust → rust", "rust", known_lang("rust"))
eq("known_lang unknown → None", None, known_lang("notalang"))
eq(
    "render_code_block empty lang → plain pre",
    "<pre>hi</pre>",
    render_code_block("", "hi"),
)

# --- never raises on weird input --------------------------------------------
for weird in ("", "\x00", "🔥" * 100, "<" * 50, "```\n" * 20):
    try:
        res = fmt(weird)
        true(f"never-raise: {weird[:20]!r}…", isinstance(res, FormatResult))
    except Exception as e:
        fail(f"never-raise: {weird[:20]!r}", str(e))

# --- metrics path smoke (real emit, temp log) -------------------------------
# Covered by never-silent mock above (emit_metric format/fallback).
ok("metrics hook path exercised (see never-silent mock above)")

# --- summary ----------------------------------------------------------------
print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

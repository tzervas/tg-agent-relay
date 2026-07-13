#!/usr/bin/env python3
"""tests/test_code_highlight.py - Offline unit tests for
lib/code_highlight.py (the pygments -> self-contained HTML document
renderer + its native MyceliumLexer - see that file's module docstring
for the design rationale and honesty tags).

NO network calls. Deterministic. Exercises:
  - the CLI arg parser (`--theme=`/`--line-numbers`/`--max-lines=`, bad
    usage)
  - the never-raise render contract (`render_code_html` returns
    `(ok, reason)`, never an exception) for: a known language (rust),
    an unknown/made-up tag (falls back to a plain-text lexer, still a
    document), an oversized block (over `max_lines`), an empty block,
    and a MOCKED pygments-import failure (never-silent skip-graceful,
    matching lib/dashboard_render.py's matplotlib-absent contract)
  - the native MyceliumLexer: keyword/type/comment token coverage on a
    sample `.myc` snippet (`myc` AND `mycelium` both resolve to it - the
    same two first-class aliases lib/format.sh's `_fmt_known_lang` uses)
  - the rendered document is a SELF-CONTAINED HTML file (inline CSS via
    `noclasses=True` - no external stylesheet reference) with real
    per-token color values
  - a real end-to-end CLI invocation (subprocess, stdin -> stdout) when
    pygments IS importable in this interpreter - skipped (not failed)
    otherwise, exactly like test_metrics_agg.py's matplotlib gate.

Run standalone: `python3 tests/test_code_highlight.py`
Called by tests/run-tests.sh (same PASS/FAIL summary style as
test_metrics_agg.py/test_usage_ingest.py - this repo's pytest-less
pattern: plain asserts, an explicit runner, exit 0 iff everything passed).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

import code_highlight as ch  # noqa: E402

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


def assert_eq(name, expected, actual) -> None:
    if expected == actual:
        ok(name)
    else:
        fail(name, f"expected: {expected!r}  actual: {actual!r}")


def assert_true(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, detail)


try:
    import pygments  # noqa: F401
    import pygments.formatters  # noqa: F401

    HAS_PYGMENTS = True
except ImportError:
    HAS_PYGMENTS = False

MYC_SAMPLE = """// nodule: example
nodule example

// an ordinary line comment
fn swap(v: Value) -> Result<Value, SwapError> {
    let x = 1
    if x == 1 {
        return v.as_dense().ok_or(SwapError::OutOfRange)
    } else {
        match x {
            0 => Option::None,
        }
    }
}
"""

print("== lib/code_highlight.py: _parse_args (CLI arg parsing) ==")
assert_eq("_parse_args: too few positional args -> None (usage error)", None, ch._parse_args(["prog", "myc"]))
assert_eq("_parse_args: no args at all -> None", None, ch._parse_args(["prog"]))

opts = ch._parse_args(["prog", "myc", "/tmp/out.html"])
assert_true("_parse_args: minimal call resolves lang/out_path", opts is not None and opts["lang"] == "myc" and opts["out_path"] == "/tmp/out.html")
assert_eq("_parse_args: theme defaults to monokai", "monokai", opts["theme"])
assert_eq("_parse_args: line_numbers defaults to False", False, opts["line_numbers"])
assert_eq("_parse_args: max_lines defaults to 60", 60, opts["max_lines"])

opts2 = ch._parse_args(["prog", "rust", "/tmp/out2.html", "--theme=dracula", "--line-numbers", "--max-lines=10"])
assert_eq("_parse_args: --theme= is honored", "dracula", opts2["theme"])
assert_eq("_parse_args: --line-numbers sets True", True, opts2["line_numbers"])
assert_eq("_parse_args: --max-lines= is honored", 10, opts2["max_lines"])

# A malformed numeric flag is ignored (default kept), never a crash.
opts3 = ch._parse_args(["prog", "rust", "/tmp/out3.html", "--max-lines=notanumber"])
assert_eq("_parse_args: a malformed --max-lines= is ignored, default kept", 60, opts3["max_lines"])

print("== lib/code_highlight.py: render_code_html() never raises ==")
with tempfile.TemporaryDirectory() as tmpdir:
    out = str(Path(tmpdir) / "empty.html")
    ok_, reason = ch.render_code_html("   \n  \n", "python", out)
    assert_eq("empty/whitespace-only code -> graceful skip, never a crash", False, ok_)
    assert_true("empty code skip reason is descriptive", "empty" in reason, reason)
    assert_true("empty-skip: no file was written", not Path(out).exists())

with tempfile.TemporaryDirectory() as tmpdir:
    out = str(Path(tmpdir) / "big.html")
    big_code = "\n".join(f"x{i} = {i}" for i in range(100))
    ok_, reason = ch.render_code_html(big_code, "python", out, max_lines=60)
    assert_eq("a block over max_lines -> graceful skip (never an unbounded document)", False, ok_)
    assert_true("oversized skip reason mentions max_lines", "max_lines" in reason, reason)
    assert_true("oversized skip: no file was written", not Path(out).exists())

# --- mocked pygments-import failure (never-silent skip-graceful, same
# --- contract lib/dashboard_render.py uses for a matplotlib-absent
# --- interpreter) - patches sys.modules so `from pygments import
# --- highlight` raises ImportError, WITHOUT actually uninstalling
# --- anything in this interpreter. Restored in a finally block. Note:
# --- Pillow is NOT part of this dependency at all (HtmlFormatter with
# --- noclasses=True is pure text generation) - a real robustness/
# --- simplicity win over an earlier (PNG-based) draft of this feature.
_saved_pygments = sys.modules.get("pygments")
try:
    sys.modules["pygments"] = None  # type: ignore[assignment]
    with tempfile.TemporaryDirectory() as tmpdir:
        out = str(Path(tmpdir) / "nopygments.html")
        ok_, reason = ch.render_code_html("print(1)", "python", out)
        assert_eq("pygments unavailable (mocked ImportError) -> graceful skip, never a crash", False, ok_)
        assert_true("unavailable-dependency skip reason is descriptive", "unavailable" in reason, reason)
finally:
    if _saved_pygments is not None:
        sys.modules["pygments"] = _saved_pygments
    else:
        sys.modules.pop("pygments", None)

print("== lib/code_highlight.py: MyceliumLexer token coverage (Declared, lexical approximation) ==")
try:
    import pygments  # noqa: F401
    _HAS_PYGMENTS = True
except ImportError:
    _HAS_PYGMENTS = False
    print("SKIP  pygments not installed for this interpreter — lexer coverage skipped (optional dep)")

if _HAS_PYGMENTS:
    for alias in ("myc", "mycelium", "MYC"):
        lexer = ch._get_lexer(alias)
        assert_eq(f"_get_lexer({alias!r}) resolves to MyceliumLexer", "Mycelium", lexer.name)

    myc_lexer = ch._get_lexer("myc")
    tokens = [(str(kind), text) for kind, text in myc_lexer.get_tokens(MYC_SAMPLE) if text.strip()]
    kind_by_text = {}
    for kind, text in tokens:
        kind_by_text.setdefault(text, kind)

    assert_true("MyceliumLexer: 'nodule' tokenized as a Keyword", kind_by_text.get("nodule", "").startswith("Token.Keyword"), str(kind_by_text.get("nodule")))
    assert_true("MyceliumLexer: 'fn' tokenized as a Keyword", kind_by_text.get("fn", "").startswith("Token.Keyword"), str(kind_by_text.get("fn")))
    assert_true("MyceliumLexer: 'swap' tokenized as a Keyword", kind_by_text.get("swap", "").startswith("Token.Keyword"), str(kind_by_text.get("swap")))
    assert_true("MyceliumLexer: 'let' tokenized as a Keyword", kind_by_text.get("let", "").startswith("Token.Keyword"), str(kind_by_text.get("let")))
    assert_true("MyceliumLexer: 'if'/'else'/'return'/'match' tokenized as Keywords", all(kind_by_text.get(k, "").startswith("Token.Keyword") for k in ("if", "else", "return", "match")))
    assert_true("MyceliumLexer: 'Value'/'Result'/'Option' tokenized as Keyword.Type", all(kind_by_text.get(t, "").startswith("Token.Keyword.Type") for t in ("Value", "Result", "Option")))
    assert_true(
        "MyceliumLexer: the '// nodule:' header line is a Comment.Special token (distinct from an ordinary '//' comment)",
        any(kind == "Token.Comment.Special" and text.startswith("// nodule:") for kind, text in tokens),
        str([t for t in tokens if "nodule:" in t[1]]),
    )
    assert_true(
        "MyceliumLexer: an ordinary '//' comment is a plain Comment.Single token",
        any(kind == "Token.Comment.Single" for kind, _ in tokens),
    )

    # A genuinely unrecognized fence tag falls back to pygments' own TextLexer -
    # still a valid lexer (never a crash), just uncolored.
    unknown_lexer = ch._get_lexer("totallymadeupxyz")
    assert_eq("_get_lexer(unknown tag) falls back to TextLexer", "Text only", unknown_lexer.name)

    empty_lexer = ch._get_lexer("")
    assert_eq("_get_lexer('') (no fence tag) falls back to TextLexer", "Text only", empty_lexer.name)

if _HAS_PYGMENTS:
    print("      (pygments importable in this interpreter - exercising the real HTML-document path)")

    with tempfile.TemporaryDirectory() as tmpdir:
        out = str(Path(tmpdir) / "myc.html")
        ok_, reason = ch.render_code_html(MYC_SAMPLE, "myc", out)
        assert_eq("myc block renders successfully", True, ok_)
        assert_true("myc render: HTML file written, non-empty", Path(out).is_file() and Path(out).stat().st_size > 0, reason)
        doc = Path(out).read_text(encoding="utf-8")
        assert_true("myc render: a real, complete HTML document (DOCTYPE + </html>)", doc.startswith("<!DOCTYPE") and doc.rstrip().endswith("</html>"))
        assert_true("myc render: CSS is INLINED (noclasses=True) - a color: value present, no external stylesheet <link>", "color:#" in doc or "color: #" in doc)
        assert_true("myc render: self-contained - no external stylesheet reference", "<link" not in doc)
        assert_true("myc render: the actual source text is present in the document", "nodule" in doc and "example" in doc)

    # --- XSS/HTML-injection regression guard (Finding 2 class): the CODE
    # --- CONTENT is already escaped by pygments today, but this is the
    # --- assertion that keeps it honest against a future pygments/
    # --- formatter change - adversarial content must never appear
    # --- unescaped in the generated (self-contained, opened-in-a-browser)
    # --- document.
    with tempfile.TemporaryDirectory() as tmpdir:
        out = str(Path(tmpdir) / "adversarial.html")
        adversarial_code = "x = '<script>alert(1)</script>'\ny = \"quote's \\\" edge-case\"\n"
        ok_, reason = ch.render_code_html(adversarial_code, "python", out)
        assert_eq("adversarial code block still renders successfully", True, ok_)
        adv_doc = Path(out).read_text(encoding="utf-8")
        assert_true("adversarial content: raw <script> tag is NOT present verbatim", "<script>alert(1)</script>" not in adv_doc, adv_doc[:2000])
        assert_true("adversarial content: the payload is present only in an escaped form (&lt;script&gt;)", "&lt;script&gt;" in adv_doc, adv_doc[:2000])

    # --- title-escaping regression guard (Finding 2): HtmlFormatter(title=)
    # --- does NOT html-escape its argument, so this module must escape
    # --- `lang` itself before building the title - independent of the
    # --- caller's own fence-tag charset restriction (defense-in-depth:
    # --- exercised here by calling render_code_html directly with a
    # --- payload the real caller's regex would never let through, to
    # --- prove THIS module doesn't rely on that upstream constraint).
    with tempfile.TemporaryDirectory() as tmpdir:
        out = str(Path(tmpdir) / "title_xss.html")
        ok_, reason = ch.render_code_html("print(1)", "<script>alert(1)</script>", out)
        assert_eq("adversarial lang/title still renders successfully (never-raise contract)", True, ok_)
        title_doc = Path(out).read_text(encoding="utf-8")
        assert_true("title escaping: raw <script> tag is NOT present verbatim (defense-in-depth XSS guard)", "<script>alert(1)</script>" not in title_doc, title_doc[:2000])
        assert_true("title escaping: the lang payload is present only in an escaped form (&lt;script&gt;)", "&lt;script&gt;" in title_doc, title_doc[:2000])

    for lang, code in (
        ("rust", 'fn main() {\n    println!("hi");\n}\n'),
        ("python", "def f(x):\n    return x + 1\n"),
        ("json", '{"a": 1, "b": [1, 2, 3]}\n'),
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = str(Path(tmpdir) / f"{lang}.html")
            ok_, reason = ch.render_code_html(code, lang, out)
            assert_eq(f"{lang} block renders successfully", True, ok_)
            assert_true(f"{lang} render: HTML file written, non-empty", Path(out).is_file() and Path(out).stat().st_size > 0, reason)

    with tempfile.TemporaryDirectory() as tmpdir:
        out = str(Path(tmpdir) / "unknown.html")
        ok_, reason = ch.render_code_html("some random text with no known language", "totallymadeupxyz", out)
        assert_eq("unknown-language block still renders (plain-text lexer, still a document)", True, ok_)
        assert_true("unknown-language render: content present", "some random text" in Path(out).read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as tmpdir:
        out_ln = str(Path(tmpdir) / "linenos.html")
        ok_, reason = ch.render_code_html("a = 1\nb = 2\n", "python", out_ln, line_numbers=True)
        assert_eq("line_numbers=True renders successfully", True, ok_)
        assert_true("line_numbers=True: a line-number gutter is present", "linenos" in Path(out_ln).read_text(encoding="utf-8"))

    print("== lib/code_highlight.py: real CLI subprocess (stdin -> stdout contract) ==")
    with tempfile.TemporaryDirectory() as tmpdir:
        out = str(Path(tmpdir) / "cli.html")
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "lib" / "code_highlight.py"), "myc", out, "--theme=dracula"],
            input=MYC_SAMPLE,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert_eq("CLI: exit code 0 on a successful render", 0, proc.returncode)
        assert_eq("CLI: stdout is exactly DOC:<path>", f"DOC:{out}\n", proc.stdout)
        assert_true("CLI: a real HTML document was written", Path(out).read_text(encoding="utf-8").startswith("<!DOCTYPE"))

    proc_bad = subprocess.run(
        [sys.executable, str(REPO_ROOT / "lib" / "code_highlight.py")],
        input="",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert_eq("CLI: bad usage (no args) -> exit code 2 (a caller bug, not a runtime condition)", 2, proc_bad.returncode)
else:
    print("SKIP  pygments not importable in this interpreter - HTML-document path/CLI subprocess not exercised here")
    print("      (never-silent: this line IS the record; the mocked-ImportError skip-graceful path above IS covered)")

# ============================================================================
print()
print("=" * 60)
print(f"Total: {PASS + FAIL}   Pass: {PASS}   Fail: {FAIL}")
if FAIL > 0:
    print(f"FAILED: {', '.join(FAILURES)}")
    sys.exit(1)
sys.exit(0)

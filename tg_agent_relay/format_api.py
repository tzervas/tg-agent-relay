"""Pure-Python HTML formatter — parity port of lib/format.sh (issue #25).

Turns plain-text relay messages into phone-readable Telegram HTML
(dynamic soft-wrap, bolded section headers, code boxes, quotes, light
emphasis). Emits only Telegram-supported HTML tags:
  <b> <i> <code> <pre> <blockquote[ expandable]>

Contract:
  format_message(text, *, enabled=True, wrap_width=50, config=None) -> FormatResult

Never raises for normal string input. On internal tag-balance failure,
falls back to escaped plain text with parse_mode=\"HTML\" and logs via
emit_metric (never-silent).
"""

from __future__ import annotations

import re
from typing import Any

from tg_agent_relay.metrics import emit_metric
from tg_agent_relay.protocols import FormatResult

# Fence open: ```optional-lang, optional trailing whitespace only.
_FENCE_OPEN_RE = re.compile(r"^```([A-Za-z0-9_+.-]*)\s*$")

# Tags this module may emit — used for the defensive balance self-check.
_CODE_OPEN_RE = re.compile(r'<code(?: class="language-[a-z0-9+.]+")?>')
_BQ_OPEN_RE = re.compile(r"<blockquote(?: expandable)?>")

# Allowlisted fence language tags (lowercased). myc/mycelium are special-
# cased in known_lang to normalize to "mycelium".
_KNOWN_LANGS: frozenset[str] = frozenset(
    {
        "myc",
        "mycelium",
        "rust",
        "python",
        "py",
        "bash",
        "sh",
        "shell",
        "zsh",
        "json",
        "yaml",
        "yml",
        "toml",
        "c",
        "cpp",
        "c++",
        "go",
        "golang",
        "js",
        "javascript",
        "ts",
        "typescript",
        "jsx",
        "tsx",
        "java",
        "kotlin",
        "kt",
        "scala",
        "swift",
        "ruby",
        "rb",
        "php",
        "sql",
        "diff",
        "patch",
        "html",
        "xml",
        "css",
        "scss",
        "less",
        "markdown",
        "md",
        "text",
        "plain",
        "plaintext",
        "dockerfile",
        "docker",
        "makefile",
        "make",
        "ini",
        "cfg",
        "conf",
        "graphql",
        "proto",
        "elixir",
        "erlang",
        "haskell",
        "clojure",
        "julia",
        "dart",
        "nim",
        "zig",
        "vim",
        "powershell",
        "ps1",
        "r",
        "lua",
        "perl",
        "hcl",
        "terraform",
    }
)

# Placeholder for spaces protected inside `code` during wrap.
# Shell uses $'\x1f' (unit separator); Python 3.14 treats U+001F as
# whitespace (str.isspace / str.split), so use a non-whitespace sentinel.
_PROTECTED_SPACE = "\x01"

_ALNUM = re.compile(r"[A-Za-z0-9]")


def escape_html(text: str) -> str:
    """Escape the three characters Telegram HTML parse_mode requires.

    Order matters: & first so entities we insert are never double-escaped.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def known_lang(lang: str) -> str | None:
    """Return normalized language tag, or None if unrecognized.

    ``myc`` / ``mycelium`` both normalize to ``mycelium``.
    """
    low = lang.lower()
    if low not in _KNOWN_LANGS:
        return None
    if low in ("myc", "mycelium"):
        return "mycelium"
    return low


def render_code_block(lang: str, body: str, *, myc_inline_lang: str = "rust") -> str:
    """HTML-escape body and box as <pre><code class=...> or plain <pre>.

    myc/mycelium fences alias to ``language-<myc_inline_lang>`` (default
    rust) so Telegram's client-side highlighter can color them. The alias
    is fail-closed: only allowlisted tags are accepted; bad config falls
    back to literal ``mycelium``.
    """
    esc = escape_html(body)
    lang_low = lang.lower()
    norm = known_lang(lang_low) if lang_low else None
    if norm is None:
        return f"<pre>{esc}</pre>"

    if norm == "mycelium":
        alias = (myc_inline_lang or "").lower()
        # Empty alias is treated like unset → default "rust" (shell cfg_get
        # [[ -n ]] falls through to the same default).
        if not alias:
            alias = "rust"
        alias_norm = known_lang(alias)
        if alias_norm is not None:
            norm = alias_norm
        # else: keep "mycelium" (fail closed — never pass unchecked)

    return f'<pre><code class="language-{norm}">{esc}</code></pre>'


def is_header_line(line: str) -> bool:
    """True for ``## `` prefix or leading-emoji ALL-CAPS / Title-Case short line."""
    if line.startswith("## "):
        return True

    # Leading run of non-printable-ASCII (emoji/symbol) + space + rest.
    # Mirrors bash: ^([^\ -~]+)\ (.*)$
    if not line:
        return False
    i = 0
    n = len(line)
    while i < n:
        o = ord(line[i])
        if 0x20 <= o <= 0x7E:
            break
        i += 1
    if i == 0 or i >= n or line[i] != " ":
        return False
    rest = line[i + 1 :]
    if not rest or len(rest) > 60:
        return False

    # ALL-CAPS short phrase: no lowercase ASCII letters, at least one upper.
    if not re.search(r"[a-z]", rest) and re.search(r"[A-Z]", rest):
        return True

    # Title-Case: every whitespace-delimited word starts with uppercase ASCII.
    return all(word and "A" <= word[0] <= "Z" for word in rest.split())


def render_emphasis(text: str) -> str:
    """*word* / _word_ → <i>escaped</i>; word-boundary-guarded char scan."""
    out: list[str] = []
    run: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch in "*_":
            close = -1
            for j in range(i + 1, length):
                if text[j] == ch:
                    close = j
                    break
            if close > i + 1:
                inner = text[i + 1 : close]
                prev_char = text[i - 1] if i > 0 else ""
                next_char = text[close + 1] if close + 1 < length else ""
                boundary_ok = True
                if prev_char and _ALNUM.match(prev_char):
                    boundary_ok = False
                if next_char and _ALNUM.match(next_char):
                    boundary_ok = False
                if boundary_ok:
                    out.append(escape_html("".join(run)))
                    run = []
                    out.append(f"<i>{escape_html(inner)}</i>")
                    i = close + 1
                    continue
        run.append(ch)
        i += 1
    out.append(escape_html("".join(run)))
    return "".join(out)


def render_inline(text: str, *, code_spans: bool = True) -> str:
    """`` `code` `` → <code>; remainder via render_emphasis. Char scan."""
    out: list[str] = []
    run: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if code_spans and ch == "`":
            close = -1
            for j in range(i + 1, length):
                if text[j] == "`":
                    close = j
                    break
            if close > i + 1:
                out.append(render_emphasis("".join(run)))
                run = []
                inner = text[i + 1 : close]
                out.append(f"<code>{escape_html(inner)}</code>")
                i = close + 1
                continue
        run.append(ch)
        i += 1
    out.append(render_emphasis("".join(run)))
    return "".join(out)


def wrap_line(line: str, width: int) -> list[str]:
    """Word-boundary soft-wrap. Protects spaces inside matched `code` spans.

    Returns one or more lines (no trailing empties). Unbreakable tokens
    longer than width stay whole on their own line.
    """
    if len(line) <= width:
        return [line]

    # Protect spaces inside backtick spans so they act as one token.
    protected: list[str] = []
    in_span = False
    for ch in line:
        if ch == "`":
            in_span = not in_span
            protected.append(ch)
        elif ch == " " and in_span:
            protected.append(_PROTECTED_SPACE)
        else:
            protected.append(ch)
    protected_s = "".join(protected)

    # bash `read -ra words <<< "$protected"` — whitespace split, drop empties.
    words = protected_s.split()
    result: list[str] = []
    cur = ""
    for w in words:
        real = w.replace(_PROTECTED_SPACE, " ")
        if not cur:
            cur = real
        elif len(cur) + 1 + len(real) <= width:
            cur = f"{cur} {real}"
        else:
            result.append(cur)
            cur = real
    if cur:
        result.append(cur)
    return result if result else [""]


def html_balanced(s: str) -> bool:
    """Count open/close for tags this module emits. Defensive self-check."""
    bo = s.count("<b>")
    bc = s.count("</b>")
    io = s.count("<i>")
    ic = s.count("</i>")
    co = len(_CODE_OPEN_RE.findall(s))
    cc = s.count("</code>")
    po = s.count("<pre>")
    pc = s.count("</pre>")
    qo = len(_BQ_OPEN_RE.findall(s))
    qc = s.count("</blockquote>")
    return bo == bc and io == ic and co == cc and po == pc and qo == qc


def _cfg_bool(cfg: dict[str, Any] | None, key: str, default: bool = True) -> bool:
    if not cfg:
        return default
    val = cfg.get("format", {}) if isinstance(cfg.get("format"), dict) else {}
    if not isinstance(val, dict) or key not in val:
        return default
    v = val[key]
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() == "true"
    return bool(v)


def _myc_inline_lang(cfg: dict[str, Any] | None) -> str:
    """Resolve [code_highlight].myc_inline_lang; empty/missing → 'rust'."""
    if not cfg:
        return "rust"
    ch = cfg.get("code_highlight")
    if not isinstance(ch, dict):
        return "rust"
    raw = ch.get("myc_inline_lang", "rust")
    if raw is None or raw == "":
        # Shell cfg_get [[ -n ]] falls through to default "rust".
        return "rust"
    return str(raw)


def _flush_quote(
    qbuf: list[str],
    out: list[str],
    *,
    blockquotes: bool,
    code_spans: bool,
) -> None:
    if not qbuf:
        return
    if blockquotes:
        joined = "\n".join(qbuf)
        esc = render_inline(joined, code_spans=code_spans)
        attr = ""
        if len(qbuf) > 3 or len(joined) > 200:
            attr = " expandable"
        out.append(f"<blockquote{attr}>{esc}</blockquote>")
    else:
        for q in qbuf:
            out.append(f"&gt; {escape_html(q)}")
    qbuf.clear()


def render(
    input_text: str,
    width: int,
    *,
    headers: bool = True,
    code_spans: bool = True,
    blockquotes: bool = True,
    soft_wrap: bool = True,
    myc_inline_lang: str = "rust",
) -> str:
    """Line-oriented state machine — port of _fmt_render."""
    out: list[str] = []
    qbuf: list[str] = []
    have_out = False
    last_blank = True
    in_code = False
    code_lang = ""
    code_lines: list[str] = []

    # Split keeping empty trailing lines behavior: shell `while read ... || [[ -n ]]`
    # iterates every line; final line without trailing newline is still processed.
    if input_text == "":
        lines: list[str] = []
    else:
        lines = input_text.split("\n")
        # If input ends with \n, split yields a trailing empty that shell
        # `while read` also consumes as empty line. Keep it.

    for line in lines:
        if in_code:
            if line == "```":
                in_code = False
                body = "\n".join(code_lines)
                if code_spans:
                    out.append(render_code_block(code_lang, body, myc_inline_lang=myc_inline_lang))
                else:
                    out.append(escape_html(body))
                code_lines = []
                code_lang = ""
                have_out = True
                last_blank = False
                continue
            code_lines.append(line)
            continue

        m = _FENCE_OPEN_RE.match(line) if code_spans else None
        if m is not None:
            if qbuf:
                _flush_quote(qbuf, out, blockquotes=blockquotes, code_spans=code_spans)
            if have_out and not last_blank:
                out.append("")
                last_blank = True
            in_code = True
            code_lang = m.group(1) or ""
            code_lines = []
            continue

        is_quote = blockquotes and (line.startswith("> ") or line == ">")
        if is_quote:
            if not qbuf and have_out and not last_blank:
                out.append("")
                last_blank = True
            qline = line[1:]  # strip leading >
            if qline.startswith(" "):
                qline = qline[1:]
            qbuf.append(qline)
            continue
        if qbuf:
            _flush_quote(qbuf, out, blockquotes=blockquotes, code_spans=code_spans)

        if line == "":
            if have_out and not last_blank:
                out.append("")
                last_blank = True
            continue

        if headers and is_header_line(line):
            text = line
            if text.startswith("## "):
                text = text[3:]
            if have_out and not last_blank:
                out.append("")
            out.append(f"<b>{render_inline(text, code_spans=code_spans)}</b>")
            have_out = True
            last_blank = False
            continue

        if soft_wrap:
            for wline in wrap_line(line, width):
                out.append(render_inline(wline, code_spans=code_spans))
        else:
            out.append(render_inline(line, code_spans=code_spans))
        have_out = True
        last_blank = False

    if qbuf:
        _flush_quote(qbuf, out, blockquotes=blockquotes, code_spans=code_spans)

    # Unclosed fence at EOF → literal escaped text, never empty <pre></pre>.
    if in_code:
        out.append(escape_html(f"```{code_lang}"))
        for cl in code_lines:
            out.append(escape_html(cl))

    return "\n".join(out)


def format_message(
    text: str,
    *,
    enabled: bool = True,
    wrap_width: int = 50,
    config: dict[str, Any] | None = None,
    parse_mode: str | None = None,
) -> FormatResult:
    """Format plain text for Telegram HTML.

    Parameters
    ----------
    text:
        Source message (plain text with optional light markup).
    enabled:
        If False, return *text* unchanged with parse_mode=\"\".
        Also honors config[format].enabled / parse_mode when *config* is set
        and the caller left *enabled* at the default True path with config.
    wrap_width:
        Soft-wrap width; values < 10 fall back to 50 (shell parity).
    config:
        Optional relay.toml-shaped dict. Used for
        ``[format].*`` toggles and ``[code_highlight].myc_inline_lang``.
    parse_mode:
        Override desired parse mode. ``\"none\"`` / ``\"MarkdownV2\"`` behave
        like shell: passthrough plain (MarkdownV2 not yet rendered).

    Returns
    -------
    FormatResult
        ``.text`` ready to send, ``.parse_mode`` either ``\"HTML\"`` or ``\"\"``.
    """
    try:
        return _format_message_impl(
            text,
            enabled=enabled,
            wrap_width=wrap_width,
            config=config,
            parse_mode=parse_mode,
        )
    except Exception as exc:  # never raise for normal (or even weird) strings
        emit_metric("format", "fallback", f"internal error: {type(exc).__name__}: {exc}")
        try:
            return FormatResult(text=escape_html(text), parse_mode="HTML")
        except Exception:
            return FormatResult(text=text, parse_mode="")


def _format_message_impl(
    text: str,
    *,
    enabled: bool,
    wrap_width: int,
    config: dict[str, Any] | None,
    parse_mode: str | None,
) -> FormatResult:
    # Config-driven master switch when a config dict is provided.
    if config is not None:
        fmt = config.get("format") if isinstance(config.get("format"), dict) else {}
        if isinstance(fmt, dict):
            cfg_enabled = fmt.get("enabled", True)
            if isinstance(cfg_enabled, str):
                cfg_enabled = cfg_enabled.lower() == "true"
            cfg_pm = fmt.get("parse_mode", "HTML")
            if cfg_enabled is False or cfg_pm == "none":
                return FormatResult(text=text, parse_mode="")
            if parse_mode is None and isinstance(cfg_pm, str):
                parse_mode = cfg_pm
            if "wrap_width" in fmt and wrap_width == 50:
                try:
                    wrap_width = int(fmt["wrap_width"])
                except TypeError, ValueError:
                    wrap_width = 50

    if not enabled:
        return FormatResult(text=text, parse_mode="")

    pm = parse_mode if parse_mode is not None else "HTML"
    if pm == "none":
        return FormatResult(text=text, parse_mode="")
    if pm == "MarkdownV2":
        emit_metric(
            "format",
            "fallback",
            "parse_mode=MarkdownV2 not yet implemented - sent as plain text",
        )
        return FormatResult(text=text, parse_mode="")
    # Unrecognized → HTML (shell default).
    pm = "HTML"

    if not isinstance(wrap_width, int) or wrap_width < 10:
        wrap_width = 50

    headers = _cfg_bool(config, "headers", True)
    code_spans = _cfg_bool(config, "code_spans", True)
    blockquotes = _cfg_bool(config, "blockquotes", True)
    soft_wrap = _cfg_bool(config, "soft_wrap", True)
    myc_alias = _myc_inline_lang(config)

    rendered = render(
        text,
        wrap_width,
        headers=headers,
        code_spans=code_spans,
        blockquotes=blockquotes,
        soft_wrap=soft_wrap,
        myc_inline_lang=myc_alias,
    )

    if html_balanced(rendered):
        return FormatResult(text=rendered, parse_mode=pm)

    emit_metric(
        "format",
        "fallback",
        "rendered HTML failed the balance check - sent as escaped plain text",
    )
    return FormatResult(text=escape_html(text), parse_mode=pm)


def main(argv: list[str] | None = None) -> int:
    """CLI: stdin → stdout text; parse_mode on second line (or --json)."""
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(prog="format_api", description="Format text as Telegram HTML")
    p.add_argument("--json", action="store_true", help="Emit {text, parse_mode} JSON")
    p.add_argument("--disabled", action="store_true", help="Passthrough (enabled=false)")
    p.add_argument("--wrap-width", type=int, default=50)
    p.add_argument("--config", default="", help="Optional JSON config path")
    args = p.parse_args(argv)

    cfg = None
    if args.config:
        try:
            with open(args.config, encoding="utf-8") as f:
                cfg = json.load(f)
        except OSError, json.JSONDecodeError:
            cfg = {}

    raw = sys.stdin.read()
    result = format_message(
        raw,
        enabled=not args.disabled,
        wrap_width=args.wrap_width,
        config=cfg,
    )
    if args.json:
        print(
            json.dumps({"text": result.text, "parse_mode": result.parse_mode}, ensure_ascii=False)
        )
    else:
        # Two-block: body then a single parse_mode line after a form-feed
        # separator so multi-line bodies stay unambiguous.
        sys.stdout.write(result.text)
        if not result.text.endswith("\n") and result.text:
            sys.stdout.write("\n")
        sys.stdout.write(f"\x0cparse_mode={result.parse_mode}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

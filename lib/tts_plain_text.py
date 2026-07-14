#!/usr/bin/env python3
"""tts_plain_text.py - strip markdown/HTML to clean spoken prose for TTS.

Reads the message text on stdin, writes a plain-text transcript on stdout
for piper/espeak. The SENT Telegram text is never changed — only the voice
input is stripped (lib/tts.sh / tg-send.sh).

Never voiced (replaced with short refs or removed):
  - inline code: `...`  (and ``...``, any equal-length backtick run)
  - fenced code: ```lang ... ``` / ~~~
  - HTML <pre>/<code>
  - URLs (http(s), www, file://, bare domain-ish paths)
  - call / tool / request IDs (UUIDs, call_*, toolu_*, long hex ids, …)
  - markdown chrome: # headers, * _ ** emphasis, > quotes, list markers,
    [k/n] page headers, leftover backticks/asterisks
  - emoji and pictographs (🔔 ✅ 🚀 flags, skin tones, ZWJ sequences, …)
    — engines often misread these as "emoji" / code points or skip oddly;
    the on-screen Telegram text is unchanged

Code/links become short references (defaults: "ref. the message for the
code" / "ref. the message for the link"); adjacent identical refs collapse
into one. --speak-code reads code bodies verbatim (escape hatch).

Never-fatal: on any error, echoes stdin so voice still speaks.
"""

from __future__ import annotations

import argparse
import html
import re
import sys


def collapse_adjacent_refs(text: str, *phrases: str) -> str:
    """Collapse consecutive identical reference phrases into one."""
    if not text or not phrases:
        return text
    ordered = sorted({p for p in phrases if p}, key=len, reverse=True)
    if not ordered:
        return text
    alt = "|".join(re.escape(p) for p in ordered)
    pattern = re.compile(
        rf"(?P<p>{alt})(?:\s*[,;.…]?\s+)(?P=p)(?:\s*[,;.…]?\s*(?P=p))*",
        re.IGNORECASE,
    )
    prev = None
    out = text
    while prev != out:
        prev = out
        out = pattern.sub(lambda m: m.group("p"), out)
    return re.sub(r"[ \t]+", " ", out).strip()


# UUID (with or without braces), common model/tool call ids
_RE_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
# Anthropic toolu_…, OpenAI call_…, generic call_id=…, toolUseId: …
_RE_CALL_PREFIX = re.compile(
    r"\b(?:call|toolu|tool_use|toolUse|msg|req|request|run|sess|session|agent)[_-]"
    r"[A-Za-z0-9][A-Za-z0-9_-]{6,}\b",
    re.IGNORECASE,
)
_RE_LABELED_ID = re.compile(
    r"\b(?:call[_-]?id|tool[_-]?use[_-]?id|toolUseId|tool_use_id|request[_-]?id|"
    r"message[_-]?id|session[_-]?id|agent[_-]?id|event[_-]?id|id)\s*[:=]\s*"
    r"[A-Za-z0-9][A-Za-z0-9_.-]{5,}\b",
    re.IGNORECASE,
)
# Long opaque hex / base64-ish tokens (not short words)
_RE_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{16,}\b")
_RE_LONG_TOKEN = re.compile(r"\b[A-Za-z0-9+/_-]{24,}={0,2}\b")

# URLs: http(s), www, file, and path-looking //host/… after strip of scheme-less
_RE_URL = re.compile(r"(?i)\b(?:https?://|ftp://|file://|www\.)[^\s<>\[\]()\"']+")
# Angle-bracket autolinks <https://...>
_RE_ANGLE_URL = re.compile(r"<(?i:https?://|ftp://|file://|www\.)[^>\s]+>")

# Emoji / pictographs for voiceover (stdlib-only; no third-party emoji pack).
# Ranges are deliberate (no mega-spans into CJK/letters). ASCII + normal
# prose Unicode (accents, curly quotes) stay. Orphan ZWJ/VS/skin tones drop.
_RE_EMOJI = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # misc symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map
    "\U0001f1e0-\U0001f1ff"  # regional indicator (flags)
    "\U0001f900-\U0001f9ff"  # supplemental symbols & pictographs
    "\U0001fa00-\U0001fa6f"  # chess etc.
    "\U0001fa70-\U0001faff"  # symbols & pictographs extended-A
    "\U0001f000-\U0001f02f"  # mahjong
    "\U0001f0a0-\U0001f0ff"  # playing cards
    "\U00002702-\U000027b0"  # dingbats
    "\U000027b1-\U000027bf"
    "\U00002600-\U000026ff"  # misc symbols (☀ ⚠ ⚡ ✅ …)
    "\U0000231a-\U0000231b"  # watch / hourglass
    "\U000023e9-\U000023f3"  # media controls
    "\U000023f8-\U000023fa"
    "\U000025aa-\U000025ab"  # small squares
    "\U000025b6\U000025c0"  # play / reverse
    "\U000025fb-\U000025fe"  # medium squares
    "\U00002b05-\U00002b07"  # arrows
    "\U00002b1b-\U00002b1c"
    "\U00002b50\U00002b55"  # star / circle
    "\U00002934-\U00002935"
    "\U00002194-\U00002199"
    "\U000021a9-\U000021aa"
    "\U00002139\U00002122"  # ℹ ™
    "\U0000203c\U00002049"  # ‼ ⁉
    "\U000000a9\U000000ae"  # © ®
    "\U00003030\U0000303d"  # wavy dash / part alternation
    "\U00003297\U00003299"  # ㊗ ㊙
    "\U0001f170-\U0001f171"  # 🅰 🅱
    "\U0001f17e-\U0001f17f"
    "\U0001f18e"
    "\U0001f191-\U0001f19a"
    "\U0001f201-\U0001f202"
    "\U0001f21a\U0001f22f"
    "\U0001f232-\U0001f23a"
    "\U0001f250-\U0001f251"
    "\U000024c2"  # Ⓜ
    "\U0000fe00-\U0000fe0f"  # variation selectors
    "\U0000200d"  # ZWJ
    "\U000020e3"  # keycap combiner
    # Tag chars U+E0020–U+E007F — must be exactly 8 hex digits after \U
    # (a 9-digit form is parsed as \Uxxxxxxxx + leftover and can create
    # an ASCII-swallowing range like '0'-…).
    "\U000e0020-\U000e007f"
    "\U0001f3fb-\U0001f3ff"  # skin tone modifiers
    "]+"
)
# Isolated leftover joiners / VS after partial multi-codepoint emoji removal
_RE_EMOJI_ORPHANS = re.compile("[\u200d\ufe0e\ufe0f\u20e3\U0001f3fb-\U0001f3ff]+")


def _strip_ids(text: str) -> str:
    """Remove call IDs / UUIDs / opaque tokens the voice should never spell out."""
    text = _RE_LABELED_ID.sub(" ", text)
    text = _RE_CALL_PREFIX.sub(" ", text)
    text = _RE_UUID.sub(" ", text)
    text = _RE_LONG_HEX.sub(" ", text)
    # Avoid nuking normal long words: only slash/plus heavy tokens (ids/hashes)
    text = re.sub(r"\b(?=[A-Za-z0-9_+/-]*[_+/-])[A-Za-z0-9_+/.-]{24,}={0,2}\b", " ", text)
    return text


def strip_emoji(text: str) -> str:
    """Remove emoji/pictographs from spoken prose. Pure; never raises."""
    if not text:
        return text
    text = _RE_EMOJI.sub(" ", text)
    text = _RE_EMOJI_ORPHANS.sub(" ", text)
    return text


def strip_formatting(
    text: str,
    code_ref: str = "ref. the message for the code",
    link_ref: str = "ref. the message for the link",
    speak_code: bool = False,
    collapse_refs: bool = True,
) -> str:
    """Return `text` as clean spoken prose. Pure; never raises for normal input."""

    # 0. Explicit fenced code blocks FIRST (``` … ```), including flattened
    #    single-line form: ```lang code here```. Prefer this over the generic
    #    equal-run rule so fences never leak as spoken ticks + code.
    def _bt_fence(m: re.Match[str]) -> str:
        body = m.group("body") or ""
        # drop optional language tag on first line of body
        body = re.sub(r"^[a-zA-Z0-9_+-]*\s*", "", body, count=1)
        return body.strip() if speak_code else code_ref

    text = re.sub(
        r"(?s)(?P<open>`{3,})(?P<body>.*?)(?P=open)",
        _bt_fence,
        text,
    )
    # Orphan triple-tick openers left after failed match
    text = re.sub(r"`{3,}[a-zA-Z0-9_+-]*", " " if speak_code else code_ref, text)

    # 1. Tilde fences ~~~
    def _tilde_fence(m: re.Match[str]) -> str:
        return (m.group("body") or "").strip() if speak_code else code_ref

    text = re.sub(
        r"(?s)(?P<f>~{3,})[^\n`]*\n?(?P<body>.*?)(?P=f)",
        _tilde_fence,
        text,
    )
    text = re.sub(r"~{3,}", " " if speak_code else code_ref, text)

    # 2. HTML <pre> / <code>
    def _pre(m: re.Match[str]) -> str:
        if speak_code:
            return re.sub(r"<[^>]+>", "", m.group("body") or "")
        return code_ref

    text = re.sub(r"(?is)<pre\b[^>]*>(?P<body>.*?)</pre>", _pre, text)

    def _htmlcode(m: re.Match[str]) -> str:
        return (m.group("body") or "") if speak_code else code_ref

    text = re.sub(r"(?is)<code\b[^>]*>(?P<body>.*?)</code>", _htmlcode, text)

    # 3. Remaining backtick spans: `inline` and ``double`` (CommonMark N-run)
    def _span(m: re.Match[str]) -> str:
        return m.group(2) if speak_code else code_ref

    text = re.sub(r"(?s)(`+)(.+?)\1", _span, text)
    # Any leftover ticks (unbalanced) — never voiced
    text = re.sub(r"`+", " ", text)

    # 4. Markdown links + autolinks — never voice the URL
    def _link(m: re.Match[str]) -> str:
        label = (m.group(1) or "").strip()
        return f"{label}, {link_ref}" if label else f"link, {link_ref}"

    text = re.sub(r"\[([^\]]*)\]\((?:[^)]*)\)", _link, text)
    text = _RE_ANGLE_URL.sub(f"link, {link_ref}", text)
    text = _RE_URL.sub(f"link, {link_ref}", text)

    # 5. Remaining HTML tags out
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)

    # 6. Call / tool / session IDs (after code/url so labeled ids in code are gone)
    text = _strip_ids(text)

    # 7. Markdown chrome (flattened-safe)
    text = re.sub(r"(?:(?<=\s)|^)#{1,6}[ \t]+", "", text)
    text = re.sub(r"(?:(?<=\s)|^)>{1,}[ \t]+", "", text)
    text = re.sub(r"(?m)^[ \t]*[-+*][ \t]+", "", text)
    text = re.sub(r"(?m)^[ \t]*\d+[.)][ \t]+", "", text)
    text = re.sub(r"\[\d+/\d+\][ \t]*", "", text)
    # **bold** / __bold__ (after *word* so nested-ish cases still shrink)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    # *emphasis* / _emphasis_ (word-boundary guarded)
    text = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])", r"\1", text)
    text = re.sub(r"(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])", r"\1", text)
    # Leftover markdown punctuation that should never be spoken as "asterisk"
    text = text.replace("*", " ")
    text = text.replace("#", " ")
    text = re.sub(r"(?<!\w)~(?!\w)", " ", text)

    # 8. Emoji / pictographs — never spoken (engines misread or spell code points)
    text = strip_emoji(text)

    # 9. Whitespace → one flowing prose line
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip()

    # 10. Collapse adjacent identical refs
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
        try:
            sys.stdout.write(raw)
        except Exception:
            pass
        return 0
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

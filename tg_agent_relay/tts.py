"""TTS prose strip + spoken_mode short/full chunk/truncate (issue #28).

Package surface for voice note text preparation. Live shell path still uses
lib/tts_plain_text.py + lib/tts.sh; this module re-exports the stripper and
adds the short-mode truncate / full-mode chunk helpers that tg-send.sh
implements in bash (_tts_truncate_words / _tts_chunk_text).

Public API
----------
  strip_formatting(...)          — markdown/HTML → spoken prose
  collapse_adjacent_refs(...)    — collapse consecutive identical ref phrases
  truncate_words(text, max)      — spoken_mode=short word-boundary truncate
  chunk_text(text, max)          — spoken_mode=full word-boundary chunking
  normalize_spoken_mode(mode)    — "short" | "full"
  prepare_spoken(text, ...)      — strip + short truncate OR full chunk → clips

Never raises for normal string input. No network. No synthesis (that is #26).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from tts_plain_text import (  # noqa: E402
    collapse_adjacent_refs,
    strip_emoji,
    strip_formatting,
)

DEFAULT_CODE_REF = "ref. the message for the code"
DEFAULT_LINK_REF = "ref. the message for the link"
DEFAULT_SPOKEN_MAX_CHARS = 600
DEFAULT_CLIP_MAX_CHARS = 1500

__all__ = [
    "DEFAULT_CLIP_MAX_CHARS",
    "DEFAULT_CODE_REF",
    "DEFAULT_LINK_REF",
    "DEFAULT_SPOKEN_MAX_CHARS",
    "SpokenClips",
    "VoicePlan",
    "chunk_text",
    "collapse_adjacent_refs",
    "effective_spoken_mode",
    "normalize_spoken_mode",
    "plan_voice_send",
    "prepare_spoken",
    "prepare_spoken_from_config",
    "strip_emoji",
    "strip_formatting",
    "truncate_words",
]


def normalize_spoken_mode(mode: str | None) -> str:
    """Map config aliases to ``short`` (default) or ``full``.

    Shell parity (tg-send.sh): full | chunk | complete → full; everything
    else (including empty/None) → short.
    """
    m = (mode or "").strip().lower()
    if m in ("full", "chunk", "complete"):
        return "full"
    return "short"


def truncate_words(text: str, max_chars: int) -> str:
    """Word-boundary truncate for ``spoken_mode=short``.

    Empty max or ``max_chars <= 0`` = no truncate (return text unchanged).
    Prefers the last space so a spoken word is never split mid-token.
    Parity with lib/tts.sh ``_tts_truncate_words``.
    """
    if not text:
        return text
    try:
        max_n = int(max_chars)
    except (TypeError, ValueError) as _exc:
        return text
    if max_n <= 0 or len(text) <= max_n:
        return text
    cut = text[:max_n]
    # Prefer last space so we don't split mid-word.
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split spoken prose into word-boundary chunks of at most ``max_chars``.

    Parity with lib/tts.sh ``_tts_chunk_text``:

    - empty text → ``[]``
    - ``max_chars <= 0`` or non-int, or text already within max → one chunk
      (byte-identical when no split needed)
    - split only on whitespace; a single oversize word is hard-split
    - full coverage: chunks re-joined with single spaces reconstruct the
      whitespace-normalized prose (strip already collapses spaces)

    Pure / deterministic; no I/O.
    """
    if not text:
        return []
    try:
        max_n = int(max_chars)
    except (TypeError, ValueError) as _exc:
        return [text]
    if max_n <= 0 or len(text) <= max_n:
        return [text]

    words = text.split()
    if not words:
        return [text]

    chunks: list[str] = []
    cur = ""
    for word in words:
        cand = f"{cur} {word}" if cur else word
        if len(cand) <= max_n:
            cur = cand
            continue
        # cand overflowed: flush what we already had
        if cur:
            chunks.append(cur)
            cur = ""
        if len(word) <= max_n:
            cur = word
        else:
            # Single word longer than max_chars: hard-split (last-resort).
            rest = word
            while len(rest) > max_n:
                chunks.append(rest[:max_n])
                rest = rest[max_n:]
            cur = rest
    if cur:
        chunks.append(cur)
    if not chunks:
        return [text]
    return chunks


@dataclass(frozen=True)
class SpokenClips:
    """Result of prepare_spoken — ordered voice clip texts + mode metadata.

    Metrics-friendly fields mirror shell emit_metric detail keys:
      truncated → hook_voice_truncated path (short mode cut)
      chunked   → hook_voice_chunked path (full mode multi-clip)
    """

    clips: tuple[str, ...]
    spoken_mode: str
    spoken_chars: int
    max_chars: int
    truncated: bool
    chunked: bool

    def as_list(self) -> list[str]:
        return list(self.clips)


def prepare_spoken(
    text: str,
    *,
    spoken_mode: str = "short",
    spoken_max_chars: int = DEFAULT_SPOKEN_MAX_CHARS,
    clip_max_chars: int = DEFAULT_CLIP_MAX_CHARS,
    code_ref: str = DEFAULT_CODE_REF,
    link_ref: str = DEFAULT_LINK_REF,
    speak_code: bool = False,
    collapse_refs: bool = True,
    strip: bool = True,
    config: dict[str, Any] | None = None,
) -> SpokenClips:
    """Strip formatting then apply ``spoken_mode`` short/full coverage.

    Parity with the hook-voice path in tg-send.sh (v0.5.4+):

    - **short** (default): truncate spoken prose to ``spoken_max_chars`` at a
      word boundary → exactly one clip (or empty).
    - **full**: cover the entire spoken prose; split at word boundaries into
      clips of ``clip_max_chars`` (0 = one unbounded clip).

    Prefer :func:`prepare_spoken_from_config` when reading a relay.toml
    JSON dict; the unused ``config`` kwarg is accepted for call-site
    symmetry and ignored here.
    """
    _ = config  # use prepare_spoken_from_config for relay.toml JSON
    mode = normalize_spoken_mode(spoken_mode)
    raw = text or ""
    if strip:
        spoken = strip_formatting(
            raw,
            code_ref=code_ref,
            link_ref=link_ref,
            speak_code=speak_code,
            collapse_refs=collapse_refs,
        )
    else:
        spoken = raw

    spoken_chars = len(spoken)

    if not spoken:
        return SpokenClips(
            clips=(),
            spoken_mode=mode,
            spoken_chars=0,
            max_chars=0,
            truncated=False,
            chunked=False,
        )

    if mode == "short":
        try:
            max_n = int(spoken_max_chars)
        except (TypeError, ValueError) as _exc:
            max_n = DEFAULT_SPOKEN_MAX_CHARS
        clipped = truncate_words(spoken, max_n)
        truncated = len(clipped) < spoken_chars and max_n > 0
        return SpokenClips(
            clips=(clipped,) if clipped else (),
            spoken_mode="short",
            spoken_chars=spoken_chars,
            max_chars=max_n if max_n > 0 else 0,
            truncated=truncated,
            chunked=False,
        )

    # full mode
    try:
        clip_max = int(clip_max_chars)
    except (TypeError, ValueError) as _exc:
        clip_max = DEFAULT_CLIP_MAX_CHARS
    clips = chunk_text(spoken, clip_max)
    return SpokenClips(
        clips=tuple(clips),
        spoken_mode="full",
        spoken_chars=spoken_chars,
        max_chars=clip_max if clip_max > 0 else 0,
        truncated=False,
        chunked=len(clips) > 1,
    )


def prepare_spoken_from_config(
    text: str,
    config: dict[str, Any] | None = None,
    **overrides: Any,
) -> SpokenClips:
    """Like :func:`prepare_spoken` but reads defaults from a relay.toml JSON dict.

    Looks under ``config["tts"]`` for spoken_mode, spoken_max_chars,
    clip_max_chars (or legacy hook_voice_max_chars), voice_code_ref,
    voice_link_ref, speak_code, collapse_adjacent_refs.
    Keyword overrides take precedence over config values.
    """
    tts: dict[str, Any] = {}
    if isinstance(config, dict):
        raw_tts = config.get("tts")
        if isinstance(raw_tts, dict):
            tts = raw_tts

    def _get(key: str, default: Any) -> Any:
        if key in overrides and overrides[key] is not None:
            return overrides[key]
        if key in tts and tts[key] is not None:
            return tts[key]
        return default

    # clip size: prefer clip_max_chars, fall back to hook_voice_max_chars
    if "clip_max_chars" in overrides and overrides["clip_max_chars"] is not None:
        clip_max = overrides["clip_max_chars"]
    elif tts.get("clip_max_chars") is not None and tts.get("clip_max_chars") != "":
        clip_max = tts["clip_max_chars"]
    elif tts.get("hook_voice_max_chars") is not None and tts.get("hook_voice_max_chars") != "":
        clip_max = tts["hook_voice_max_chars"]
    else:
        clip_max = DEFAULT_CLIP_MAX_CHARS

    speak_code = _get("speak_code", False)
    if isinstance(speak_code, str):
        speak_code = speak_code.strip().lower() in ("true", "1", "yes", "on")

    collapse = _get("collapse_refs", None)
    if collapse is None:
        collapse = _get("collapse_adjacent_refs", True)
    if isinstance(collapse, str):
        collapse = collapse.strip().lower() not in ("false", "0", "no", "off")

    def _as_int(val: Any, default: int) -> int:
        try:
            return int(val)
        except (TypeError, ValueError) as _exc:
            return default

    code_ref = _get("code_ref", None)
    if code_ref is None:
        code_ref = _get("voice_code_ref", DEFAULT_CODE_REF)
    link_ref = _get("link_ref", None)
    if link_ref is None:
        link_ref = _get("voice_link_ref", DEFAULT_LINK_REF)

    return prepare_spoken(
        text,
        spoken_mode=str(_get("spoken_mode", "short")),
        spoken_max_chars=_as_int(
            _get("spoken_max_chars", DEFAULT_SPOKEN_MAX_CHARS), DEFAULT_SPOKEN_MAX_CHARS
        ),
        clip_max_chars=_as_int(clip_max, DEFAULT_CLIP_MAX_CHARS),
        code_ref=str(code_ref if code_ref is not None else DEFAULT_CODE_REF),
        link_ref=str(link_ref if link_ref is not None else DEFAULT_LINK_REF),
        speak_code=bool(speak_code),
        collapse_refs=bool(collapse),
        strip=bool(_get("strip", True)),
    )


@dataclass(frozen=True)
class VoicePlan:
    """Whether to synthesize voice and which spoken_mode/chunk sizing to use."""

    eligible: bool
    spoken_mode: str
    chunk_max: int


def effective_spoken_mode(
    text: str,
    *,
    configured: str | None,
    spoken_max_chars: int,
    hook_event: str | None = None,
    total_pages: int = 1,
) -> str:
    """Auto-upgrade short→full for PLAN, multi-page, or very long bodies."""
    mode = normalize_spoken_mode(configured)
    if mode == "full":
        return "full"
    try:
        from tg_agent_relay.comms_format import MessageKind, classify_message

        kind = classify_message(text, hook_event)
        if kind == MessageKind.PLAN or total_pages > 1:
            return "full"
    except Exception:
        pass
    try:
        sm = int(spoken_max_chars)
    except (TypeError, ValueError) as _exc:
        sm = DEFAULT_SPOKEN_MAX_CHARS
    if sm > 0 and len(text or "") > sm * 2:
        return "full"
    return "short"


def plan_voice_send(
    msg: str,
    *,
    tts_mode: str,
    is_hook: bool,
    hook_voice: bool,
    total_pages: int,
    tts_max_chars: int,
    spoken_mode_cfg: str | None,
    spoken_max_chars: int,
    clip_max_chars: int,
    hook_event: str | None = None,
) -> VoicePlan:
    """Eligibility helper shared by Python send and unit tests."""
    mode_s = (tts_mode or "").lower()
    if mode_s not in ("text+voice", "voice-only"):
        return VoicePlan(False, "short", 0)
    mode = effective_spoken_mode(
        msg,
        configured=spoken_mode_cfg,
        spoken_max_chars=spoken_max_chars,
        hook_event=hook_event,
        total_pages=total_pages,
    )
    try:
        clip_i = int(clip_max_chars)
    except (TypeError, ValueError) as _exc:
        clip_i = DEFAULT_CLIP_MAX_CHARS
    chunk_max = clip_i if mode == "full" else 0
    if is_hook and hook_voice and msg:
        return VoicePlan(True, mode, chunk_max)
    if mode == "full" and msg:
        return VoicePlan(True, mode, chunk_max)
    if total_pages == 1 and len(msg) <= tts_max_chars:
        return VoicePlan(True, mode, 0)
    return VoicePlan(False, mode, 0)

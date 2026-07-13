"""TTS prose strip — re-export lib/tts_plain_text for package consumers."""

from __future__ import annotations

import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[1] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from tts_plain_text import collapse_adjacent_refs, strip_formatting  # noqa: E402

__all__ = ["collapse_adjacent_refs", "strip_formatting"]

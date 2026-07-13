"""Format API stub — swarm agents implement format_message here (issue #25).

Until the port lands, this raises NotImplementedError. Contract matches
lib/format.sh format_message → FMT_TEXT + FMT_PARSE_MODE.
"""

from __future__ import annotations

from tg_agent_relay.protocols import FormatResult


def format_message(text: str, *, enabled: bool = True, wrap_width: int = 50) -> FormatResult:
    """Format plain text for Telegram HTML.

    Contract (for implementers of #25):
    - enabled=False → return FormatResult(text=text, parse_mode="")
    - else apply soft-wrap, headers, fences, blockquotes, emphasis
    - on internal failure → escaped plain text with parse_mode="HTML"
    - never raise for normal string input
    """
    if not enabled:
        return FormatResult(text=text, parse_mode="")
    # Placeholder: passthrough until full port. Shell format.sh remains live.
    return FormatResult(text=text, parse_mode="")

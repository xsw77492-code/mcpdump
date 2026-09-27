"""Display width for East Asian characters, and padding that respects it.

``len()`` is not the width a terminal gives a string: fullwidth characters take
two columns and combining marks take none. Rich handles CJK width inside table
cells only, so the arithmetic lives here.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "char_width",
    "display_width",
    "ljust",
    "rjust",
    "center",
    "truncate",
    "strip_ansi",
]

# Zero-width characters: rendered but taking no column.
_ZERO_WIDTH_CODEPOINTS = frozenset(
    {
        0x200B,  # ZERO WIDTH SPACE
        0x200C,  # ZERO WIDTH NON-JOINER
        0x200D,  # ZERO WIDTH JOINER
        0x200E,  # LEFT-TO-RIGHT MARK
        0x200F,  # RIGHT-TO-LEFT MARK
        0x2060,  # WORD JOINER
        0xFEFF,  # ZERO WIDTH NO-BREAK SPACE
    }
)

# Combining and format Unicode categories: no column.
_ZERO_WIDTH_CATEGORIES = frozenset({"Mn", "Me", "Cf"})

# ANSI escapes: CSI (colour, cursor) and OSC (terminal title). They must be
# stripped before measuring, or \x1b[31m counts as five columns.
_ANSI_RE = re.compile(
    r"""
    \x1b\[[0-9;?]*[ -/]*[@-~]      # CSI ... final byte
    | \x1b\][^\x07\x1b]*(?:\x07|\x1b\\)   # OSC ... BEL or ST
    | \x1b[@-Z\\-_]                # other two-character escapes
    """,
    re.VERBOSE,
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences."""
    return _ANSI_RE.sub("", text)


def char_width(ch: str) -> int:
    """Display width of one character: 0, 1 (halfwidth) or 2 (fullwidth)."""
    code = ord(ch)

    if code == 0 or code in _ZERO_WIDTH_CODEPOINTS:
        return 0

    # Combining marks (the accent in é) stack on the previous character.
    if unicodedata.combining(ch):
        return 0

    if unicodedata.category(ch) in _ZERO_WIDTH_CATEGORIES:
        return 0

    # Control characters (\n, \t) take no column; callers split lines first.
    if unicodedata.category(ch) == "Cc":
        return 0

    # Wide/Fullwidth take 2 columns; Ambiguous is treated as 1 in a monospaced
    # terminal (emoji excepted).
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2

    return 1


def display_width(text: str) -> int:
    """Columns the string occupies in a monospaced terminal (ANSI stripped)."""
    if not text:
        return 0
    return sum(char_width(ch) for ch in strip_ansi(text))


def _pad(text: str, width: int, *, right: bool) -> str:
    """Pad to a display width. Text that is already wider is returned as-is."""
    filler = " " * max(0, width - display_width(text))
    return filler + text if right else text + filler


def ljust(text: str, width: int) -> str:
    """Pad on the right to a display width."""
    return _pad(text, width, right=False)


def rjust(text: str, width: int) -> str:
    """Pad on the left to a display width."""
    return _pad(text, width, right=True)


def center(text: str, width: int) -> str:
    """Centre within a display width. An odd extra column goes on the right."""
    gap = width - display_width(text)
    if gap <= 0:
        return text
    left = gap // 2
    return " " * left + text + " " * (gap - left)


def truncate(text: str, width: int, *, ellipsis: str = "…") -> str:
    """Truncate to a display width, ending with an ellipsis when cut.

    The ellipsis comes out of the budget: counting characters instead of columns
    is wrong — four CJK characters (``"中文中文"``) are 8 columns wide.
    """
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text

    ellipsis_width = display_width(ellipsis)
    if width <= ellipsis_width:
        return ellipsis[:width] if ellipsis_width else ""

    budget = width - ellipsis_width
    used = 0
    out: list[str] = []
    for ch in text:
        w = char_width(ch)
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out) + ellipsis

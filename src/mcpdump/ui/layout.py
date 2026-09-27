"""Spatial rules for output: the three-part skeleton and alignment primitives.

Every command's output follows the same skeleton: identity block, data block,
hints block.

This module produces plain text only and does not import Rich: ``NO_COLOR``,
redirection to a file, and the TUI's line addressing all need text without ANSI.
Callers wrap it in ``Text`` for colour.

No user-facing strings are hardcoded; callers pass them in.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .symbols import SYMBOLS
from .width import display_width, ljust, truncate

__all__ = [
    "INDENT",
    "HINT_MARK",
    "end_gap",
    "justify_ends",
    "key_value_lines",
    "section_lines",
    "hint_lines",
    "indent_lines",
    "bullet_lines",
    "join_blocks",
]

#: Default indent unit. Four spaces is too wide in a terminal, one shows no level.
INDENT = "  "

#: Leading symbol for hints and options. Shared with the selected row in a list,
#: because one symbol has one meaning: it points at something.
HINT_MARK = SYMBOLS.selected


def indent_lines(lines: Iterable[str], level: int = 1) -> list[str]:
    """Indent every line. Blank lines stay blank, so no trailing spaces appear."""
    pad = INDENT * max(0, level)
    return [pad + line if line else "" for line in lines]


def key_value_lines(
    pairs: Iterable[tuple[str, str | None]],
    *,
    key_width: int | None = None,
    gap: int = 2,
) -> list[str]:
    """Render key/value pairs as lines aligned on display width.

    ``key_width`` defaults to the widest key. ``len()`` will not do: a CJK key
    of four characters takes eight columns while ``"name"`` takes four, so
    aligning by character count misplaces every wide key.
    """
    rows = [(str(k), "" if v is None else str(v)) for k, v in pairs]
    if not rows:
        return []

    if key_width is None:
        key_width = max(display_width(k) for k, _ in rows)
    key_width = max(key_width, 0)

    out: list[str] = []
    for key, value in rows:
        padded = ljust(key, key_width)
        # Values may span lines; continuation lines hang at the same column.
        lines = value.split("\n") if value else [""]
        out.append(padded + " " * gap + lines[0])
        for extra in lines[1:]:
            out.append(" " * (key_width + gap) + extra)
    return out


def section_lines(
    title: str,
    body: Sequence[str],
    *,
    rule: str | None = "─",
) -> list[str]:
    """A titled block: title, an optional rule, then the body.

    The rule matches the title's display width, so a wide title is neither
    over- nor under-ruled.
    """
    out = [title]
    if rule:
        out.append(rule * display_width(title))
    out.extend(body)
    return out


def hint_lines(hints: Iterable[str], *, mark: str | None = None) -> list[str]:
    """The hints block, each line starting with the pointer symbol.

    Empty strings are skipped, since callers build hints conditionally and
    should not end up with a line holding nothing but a symbol.
    """
    prefix = mark if mark is not None else HINT_MARK
    return [f"{prefix} {h}" for h in hints if h]


def bullet_lines(
    items: Iterable[str],
    *,
    mark: str | None = None,
    max_width: int | None = None,
    indent: int = 0,
) -> list[str]:
    """A symbol-prefixed list. ``max_width`` truncates each item by display width."""
    prefix = mark if mark is not None else HINT_MARK
    pad = INDENT * max(0, indent)
    out: list[str] = []
    for item in items:
        text = item
        if max_width is not None:
            text = truncate(item, max_width - display_width(pad + prefix + " "))
        out.append(f"{pad}{prefix} {text}")
    return out


def end_gap(left: str, right: str, *, width: int, gap: int = 2) -> int:
    """Spaces to insert between two ends when justifying.

    Returns ``gap`` rather than 0 when space runs out: letting the line overflow
    is better than dropping the right end, usually a duration or a count.
    """
    return max(gap, width - display_width(left) - display_width(right))


def justify_ends(left: str, right: str, *, width: int, gap: int = 2) -> str:
    """Justify to both ends: ``left`` at the start, ``right`` at the end."""
    return left + " " * end_gap(left, right, width=width, gap=gap) + right


def join_blocks(*blocks: Sequence[str], gap: int = 1) -> list[str]:
    """Concatenate blocks with ``gap`` blank lines between them.

    This is where the three-part skeleton lands: pass the identity, data and
    hints blocks in order and the blank lines take care of themselves.
    """
    out: list[str] = []
    for block in blocks:
        lines = list(block)
        if not lines:
            continue
        if out:
            out.extend([""] * max(0, gap))
        out.extend(lines)
    return out

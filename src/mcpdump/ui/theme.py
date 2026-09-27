"""Terminal visual conventions.

Colour carries meaning (success, warning, error, type) and nothing else.
"""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

THEME = Theme(
    {
        "mcpdump.brand": "bold cyan",
        "mcpdump.dim": "dim",
        "mcpdump.ok": "green",
        "mcpdump.warn": "yellow",
        "mcpdump.err": "bold red",
        "mcpdump.label": "bold",
        "mcpdump.meta": "dim cyan",
        "mcpdump.type": "magenta",
        "mcpdump.req": "blue",
        "mcpdump.res": "green",
        "mcpdump.notify": "yellow",
    }
)


def make_console(*, stderr: bool = False, width: int | None = None) -> Console:
    """Build a console. Tests and child processes pin ``width`` for stable output."""
    return Console(theme=THEME, stderr=stderr, width=width, highlight=False, soft_wrap=False)


console = make_console()
err_console = make_console(stderr=True)

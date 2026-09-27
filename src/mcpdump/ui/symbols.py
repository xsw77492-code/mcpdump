"""One symbol language: a symbol means the same thing in every command.

| key | Unicode | meaning |
|---|---|---|
| ``request`` | → | frame sent to the server |
| ``response`` | ← | frame returned by the server |
| ``notify`` | ⇢ | one-way notification, no response |
| ``available`` | ● | capability available |
| ``unavailable`` | ○ | capability unavailable |
| ``selected`` | ▸ | points at something: the current row, or the next action |
| ``warning`` | ⚠ | can continue, but pay attention |
| ``failure`` | ✗ | failed |
| ``success`` | ✓ | succeeded |
| ``pending`` | · | in progress / placeholder |
| ``branch`` | ├ | tree branch |
| ``last_branch`` | └ | last tree branch |

``selected`` doubles as list selection and as the next-step hint: one symbol,
one meaning.

``MCPDUMP_ASCII=1`` falls back to ASCII for terminals that cannot render Unicode.
"""

from __future__ import annotations

import os

__all__ = ["Symbols", "SYMBOLS", "use_ascii"]

_UNICODE = {
    "request": "→",
    "response": "←",
    "notify": "⇢",
    "available": "●",
    "unavailable": "○",
    "selected": "▸",
    "warning": "⚠",
    "failure": "✗",
    "success": "✓",
    "pending": "·",
    "branch": "├",
    "last_branch": "└",
}

_ASCII = {
    "request": "->",
    "response": "<-",
    "notify": "~~",
    "available": "*",
    "unavailable": "o",
    "selected": ">",
    "warning": "!",
    "failure": "x",
    "success": "v",
    "pending": ".",
    "branch": "|",
    "last_branch": "`",
}


class Symbols:
    """Symbol table, accessed by attribute to avoid misspelled strings."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._m = mapping

    def __getattr__(self, name: str) -> str:
        try:
            return self._m[name]
        except KeyError as exc:  # pragma: no cover - surfaces typos immediately
            raise AttributeError(f"unknown symbol: {name!r}") from exc

    def as_dict(self) -> dict[str, str]:
        return dict(self._m)


def use_ascii() -> bool:
    """Whether to fall back to ASCII, from ``MCPDUMP_ASCII`` (1/true/yes/on)."""
    return os.environ.get("MCPDUMP_ASCII", "").strip().lower() in {"1", "true", "yes", "on"}


def _build() -> Symbols:
    return Symbols(_ASCII if use_ascii() else _UNICODE)


#: Module-level symbol table. ``use_ascii()`` is evaluated once at import time;
#: call ``_build()`` again to switch.
SYMBOLS = _build()

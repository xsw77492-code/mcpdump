"""Single-key input, with the platform details kept out of the way.

Windows uses ``msvcrt``; POSIX puts the terminal into raw mode and decodes
escape sequences by hand. ``curses`` is not an option (the stdlib does not ship
it on Windows), and ``termios`` / ``tty`` must be imported lazily because they
do not exist there at all.

The platform is injected rather than read from ``sys.platform``, so the POSIX
branch stays testable on Windows. Decoding is a pure function; the key map --
``\\xe0H`` and ``\\x1b[A`` both mean up -- is the only part that can be wrong.
"""

from __future__ import annotations

import os
import select
import sys
import time
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Key",
    "KeyPress",
    "KeyReader",
    "PosixKeyReader",
    "PosixModules",
    "WindowsKeyReader",
    "decode_posix",
    "decode_windows",
    "needs_second_byte",
    "select_key_reader",
]


class Key(str, Enum):
    """The meaning of a key press; raw bytes are not kept.

    ``CHAR`` covers printable characters, with the actual character carried by
    ``KeyPress.char``.
    """

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    HOME = "home"
    END = "end"
    PAGE_UP = "page_up"
    PAGE_DOWN = "page_down"
    ENTER = "enter"
    TAB = "tab"
    ESC = "esc"
    BACKSPACE = "backspace"
    DELETE = "delete"
    CHAR = "char"
    #: An unrecognised byte sequence. Reported as such rather than guessed at:
    #: a wrong guess makes the cursor jump for no visible reason.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class KeyPress:
    """One key press. ``char`` only means anything when ``key is Key.CHAR``."""

    key: Key
    char: str = ""

    @property
    def is_printable(self) -> bool:
        return self.key is Key.CHAR and self.char.isprintable()


@runtime_checkable
class KeyReader(Protocol):
    """A source of key presses. ``read`` returning ``None`` means no key arrived
    within the timeout.

    A timeout and an unrecognised key are different: the first wants a repaint,
    the second wants nothing.
    """

    def read(self, timeout: float = 0.1) -> KeyPress | None: ...

    def close(self) -> None: ...


# ---------------------------------------------------------------- decoding

#: On Windows ``msvcrt`` gives a special key as two bytes: a prefix, then the
#: key. The second byte must be consumed, or it stays in the buffer and the next
#: read sees a lone ``H`` as the letter h.
_WINDOWS_SPECIAL_PREFIXES = frozenset({"\x00", "\xe0"})

_WINDOWS_SPECIAL = {
    "H": Key.UP,
    "P": Key.DOWN,
    "K": Key.LEFT,
    "M": Key.RIGHT,
    "G": Key.HOME,
    "O": Key.END,
    "S": Key.DELETE,
    "I": Key.PAGE_UP,
    "Q": Key.PAGE_DOWN,
}

#: POSIX CSI sequences: everything after ``\x1b[`` up to the final byte.
_POSIX_CSI = {
    "A": Key.UP,
    "B": Key.DOWN,
    "C": Key.RIGHT,
    "D": Key.LEFT,
    "H": Key.HOME,
    "F": Key.END,
    "1~": Key.HOME,
    "4~": Key.END,
    "3~": Key.DELETE,
    "5~": Key.PAGE_UP,
    "6~": Key.PAGE_DOWN,
}


def needs_second_byte(first: str) -> bool:
    """Whether this first byte starts a two-byte key on Windows."""
    return first in _WINDOWS_SPECIAL_PREFIXES


def _control_press(ch: str) -> KeyPress | None:
    """Control-character map shared by both platforms, or ``None``.

    Enter must accept both spellings: raw mode usually yields ``\\r``, while
    ``msvcrt`` may yield ``\\n``.
    """
    if ch in ("\r", "\n"):
        return KeyPress(Key.ENTER)
    if ch == "\t":
        return KeyPress(Key.TAB)
    if ch in ("\x08", "\x7f"):
        return KeyPress(Key.BACKSPACE)
    if ch == "\x1b":
        return KeyPress(Key.ESC)
    return None


def decode_windows(first: str, second: str | None = None) -> KeyPress:
    """Decode one Windows key. ``second`` is only set when the first byte starts
    a two-byte key.

    A prefix with no second byte returns ``UNKNOWN``: the prefix is not
    printable, and passing it on would deliver an invisible keystroke.
    """
    if needs_second_byte(first):
        if second is None:
            return KeyPress(Key.UNKNOWN)
        return KeyPress(_WINDOWS_SPECIAL.get(second, Key.UNKNOWN))

    control = _control_press(first)
    if control is not None:
        return control
    if first.isprintable() and first:
        return KeyPress(Key.CHAR, first)
    return KeyPress(Key.UNKNOWN)


def decode_posix(sequence: str) -> KeyPress:
    """Decode one POSIX key or escape sequence; ``sequence`` is everything read.

    A lone ``\\x1b`` is ESC and a ``\\x1b[`` prefix starts a function key; only
    the presence of further bytes tells them apart, and the reader settles that
    with ``select``.
    """
    if not sequence:
        return KeyPress(Key.UNKNOWN)

    if sequence.startswith("\x1b["):
        return KeyPress(_POSIX_CSI.get(sequence[2:], Key.UNKNOWN))
    if sequence.startswith("\x1bO"):
        # Some terminals (xterm in application mode) use the SS3 prefix.
        return KeyPress(_POSIX_CSI.get(sequence[2:], Key.UNKNOWN))

    control = _control_press(sequence)
    if control is not None:
        return control
    if len(sequence) == 1 and sequence.isprintable():
        return KeyPress(Key.CHAR, sequence)
    return KeyPress(Key.UNKNOWN)


# --------------------------------------------------------------- readers


class WindowsKeyReader:
    """Read single keys with ``msvcrt``. No dependency, no terminal mode change.

    Polls with ``kbhit`` rather than blocking on ``getch``, so the main loop can
    still repaint when nothing is pressed.
    """

    #: Poll interval. 10 ms means at most 10 ms of input latency, and ``kbhit``
    #: is a single syscall, so the CPU cost is negligible.
    _POLL_SECONDS = 0.01

    #: Upper bound on waiting for a special key's second byte; real sequences
    #: arrive together.
    _SECOND_BYTE_TIMEOUT = 0.05

    def __init__(self, *, msvcrt_module: Any = None) -> None:
        if msvcrt_module is None:
            import msvcrt  # noqa: PLC0415 - Windows-only, imported on demand

            msvcrt_module = msvcrt
        self._msvcrt = msvcrt_module
        #: ``getwch`` returns ``str`` and ``getch`` returns ``bytes``; the former
        #: is preferred because it is the only one that handles non-ASCII input.
        self._get = getattr(msvcrt_module, "getwch", None) or msvcrt_module.getch

    def read(self, timeout: float = 0.1) -> KeyPress | None:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if self._msvcrt.kbhit():
                return decode_windows(*self._read_bytes())
            if time.monotonic() >= deadline:
                return None
            time.sleep(self._POLL_SECONDS)

    def _read_bytes(self) -> tuple[str, str | None]:
        """Read one key. A special key needs both bytes, or the second one leaks
        into the next read.
        """
        first = self._as_text(self._get())
        if not needs_second_byte(first):
            return first, None
        # Waiting for the second byte is bounded: real sequences arrive together.
        # Waiting forever would freeze the UI, and treating the prefix as a
        # character would deliver an invisible keystroke.
        deadline = time.monotonic() + self._SECOND_BYTE_TIMEOUT
        while not self._msvcrt.kbhit():
            if time.monotonic() >= deadline:
                return first, None
            time.sleep(self._POLL_SECONDS)
        return first, self._as_text(self._get())

    @staticmethod
    def _as_text(raw: str | bytes) -> str:
        if isinstance(raw, bytes):
            return raw.decode("utf-8", "replace")
        return raw

    def close(self) -> None:
        """Nothing to restore: ``msvcrt`` does not change the terminal mode."""
        return None


@dataclass(frozen=True)
class PosixModules:
    """The four modules the POSIX branch needs, bundled into one injection point.

    Public because ``termios`` and ``tty`` cannot be imported on Windows at all,
    which makes injection the only way to test that branch.
    """

    termios: Any
    tty: Any
    select: Any
    os: Any


def _load_posix_modules() -> PosixModules:
    """Imported inside the function: ``termios`` and ``tty`` do not exist on
    Windows, so a module-level import would break the package there.
    """
    import termios  # noqa: PLC0415
    import tty  # noqa: PLC0415

    return PosixModules(termios=termios, tty=tty, select=select, os=os)


class PosixKeyReader:
    """Put the terminal into raw mode and read keys byte by byte.

    Raw mode disables line buffering and echo; failing to restore it leaves the
    terminal echoing nothing. Restoration lives in ``close`` and also runs on the
    exception path via the context manager.
    """

    #: How long to wait for further bytes in an escape sequence, after which it
    #: is taken as a lone ESC. Must stay small: pressing ESC should feel instant.
    _ESCAPE_TIMEOUT = 0.05

    def __init__(
        self,
        *,
        stdin: Any = None,
        modules: PosixModules | None = None,
    ) -> None:
        self._stdin = stdin if stdin is not None else sys.stdin
        #: Injected modules, for tests. When ``None`` the import is deferred to
        #: the first read, which would otherwise raise on Windows.
        self._injected = modules
        self._modules: PosixModules | None = None
        self._saved: Any = None

    @property
    def _m(self) -> PosixModules:
        if self._modules is None:
            self._modules = self._injected or _load_posix_modules()
        return self._modules

    def __enter__(self) -> PosixKeyReader:
        self._enter_raw()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _enter_raw(self) -> None:
        if self._saved is not None:
            return
        fd = self._stdin.fileno()
        self._saved = self._m.termios.tcgetattr(fd)
        self._m.tty.setraw(fd)

    def read(self, timeout: float = 0.1) -> KeyPress | None:
        self._enter_raw()
        fd = self._stdin.fileno()
        first = self._read_one(fd, timeout)
        if first is None:
            return None
        return decode_posix(self._extend(fd, first))

    def _read_one(self, fd: int, timeout: float) -> str | None:
        ready, _, _ = self._m.select.select([fd], [], [], timeout)
        if not ready:
            return None
        # Annotated explicitly: the injected ``os`` is ``Any``, so without this
        # the decoded value would leak out of the return type as ``Any``.
        raw: bytes = self._m.os.read(fd, 1)
        return raw.decode("utf-8", "replace")

    def _extend(self, fd: int, first: str) -> str:
        """Collect the remaining bytes when the sequence starts with ``\\x1b``.

        Length varies (``\\x1b[A`` is three bytes, ``\\x1b[5~`` is four), so bytes
        are read until a letter or ``~`` arrives, both of which are final bytes.
        """
        if not first.startswith("\x1b"):
            return first
        buf = first
        while not (len(buf) >= 2 and (buf[-1].isalpha() or buf[-1] == "~")):
            nxt = self._read_one(fd, self._ESCAPE_TIMEOUT)
            if nxt is None:
                break
            buf += nxt
            if len(buf) > 8:  # Real sequences are short; longer means garbage.
                break
        return buf

    def close(self) -> None:
        saved, self._saved = self._saved, None
        if saved is None:
            return
        try:
            self._m.termios.tcsetattr(
                self._stdin.fileno(), self._m.termios.TCSADRAIN, saved
            )
        except Exception:
            # A failed restore must not crash the exit path; the terminal is
            # closing anyway.
            pass


def select_key_reader(name: str | None = None) -> KeyReader:
    """Pick a reader by platform name, defaulting to ``os.name``.

    The platform name is a parameter rather than a branch so the POSIX path can
    be exercised on Windows, where ``termios`` cannot be imported. Neither
    implementation imports its platform modules at construction time.
    """
    target = os.name if name is None else name
    if target == "nt":
        return WindowsKeyReader()
    return PosixKeyReader()

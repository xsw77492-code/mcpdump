"""Tests for ``ui/keys.py``: key decoding and the two platform branches.

Decoding is a pure function asserted on real byte sequences (``\\xe0H`` and ``\\x1b[A`` are
both Up). The POSIX branch cannot run on Windows, so it is tested through injected fakes.
"""

from __future__ import annotations

import os

import pytest

from mcpdump.ui import (
    Key,
    KeyPress,
    KeyReader,
    PosixKeyReader,
    PosixModules,
    WindowsKeyReader,
    decode_posix,
    decode_windows,
    needs_second_byte,
    select_key_reader,
)


def _press(reader: KeyReader, timeout: float = 0.1) -> KeyPress:
    """Read one key and assert that one was read.

    ``read`` returns ``KeyPress | None``, and ``None`` means a timeout, which is another
    test's business.
    """
    press = reader.read(timeout)
    assert press is not None
    return press


# ---------------------------------------------------------------- Windows decoding


class TestWindowsDecoding:
    """The key shapes ``msvcrt`` produces."""

    def test_a_printable_char(self) -> None:
        assert decode_windows("a") == KeyPress(Key.CHAR, "a")

    def test_a_non_ascii_char_survives(self) -> None:
        """CJK input relies on ``getwch`` — the wide-character variant. Degrading it
        to a question mark is as good as losing it."""
        assert decode_windows("中") == KeyPress(Key.CHAR, "中")

    @pytest.mark.parametrize("raw", ["\r", "\n"])
    def test_enter_has_two_forms(self, raw: str) -> None:
        """Raw mode gives ``\\r``, ``msvcrt`` sometimes gives ``\\n``. Accepting only
        one makes Enter fail intermittently."""
        assert decode_windows(raw).key is Key.ENTER

    def test_tab(self) -> None:
        assert decode_windows("\t").key is Key.TAB

    @pytest.mark.parametrize("raw", ["\x08", "\x7f"])
    def test_backspace_has_two_forms(self, raw: str) -> None:
        assert decode_windows(raw).key is Key.BACKSPACE

    def test_a_lone_escape(self) -> None:
        assert decode_windows("\x1b").key is Key.ESC

    @pytest.mark.parametrize(
        ("second", "expected"),
        [
            ("H", Key.UP),
            ("P", Key.DOWN),
            ("K", Key.LEFT),
            ("M", Key.RIGHT),
            ("G", Key.HOME),
            ("O", Key.END),
            ("S", Key.DELETE),
            ("I", Key.PAGE_UP),
            ("Q", Key.PAGE_DOWN),
        ],
    )
    def test_special_keys_behind_the_e0_prefix(self, second: str, expected: Key) -> None:
        assert decode_windows("\xe0", second).key is expected

    def test_the_null_prefix_also_means_special(self) -> None:
        """Both prefixes must be accepted: terminals and keyboard layouts differ, and
        accepting only one drops the arrow keys."""
        assert decode_windows("\x00", "H").key is Key.UP

    def test_an_unmapped_special_key_is_unknown(self) -> None:
        """Unrecognised means "unknown". Guessing some arrow key makes the cursor jump
        for no reason."""
        assert decode_windows("\xe0", "Z").key is Key.UNKNOWN

    def test_a_half_sequence_is_unknown_not_a_char(self) -> None:
        """When the prefix never gets its second byte, it must **not** be treated as
        a char — it is unprintable."""
        assert decode_windows("\xe0").key is Key.UNKNOWN

    def test_an_unprintable_control_char_is_unknown(self) -> None:
        assert decode_windows("\x01").key is Key.UNKNOWN


class TestSecondBytePredicate:
    """The reader layer uses it to decide whether to read one more byte. Missing one
    lets the second byte pollute the next read."""

    @pytest.mark.parametrize("prefix", ["\x00", "\xe0"])
    def test_prefixes_need_a_second_byte(self, prefix: str) -> None:
        assert needs_second_byte(prefix) is True

    @pytest.mark.parametrize("ch", ["a", "\r", "\t", "\x1b"])
    def test_ordinary_keys_do_not(self, ch: str) -> None:
        assert needs_second_byte(ch) is False


# ---------------------------------------------------------------- POSIX decoding


class TestPosixDecoding:
    """Escape-sequence shapes."""

    @pytest.mark.parametrize(
        ("sequence", "expected"),
        [
            ("\x1b[A", Key.UP),
            ("\x1b[B", Key.DOWN),
            ("\x1b[C", Key.RIGHT),
            ("\x1b[D", Key.LEFT),
            ("\x1b[H", Key.HOME),
            ("\x1b[F", Key.END),
            ("\x1b[1~", Key.HOME),
            ("\x1b[4~", Key.END),
            ("\x1b[3~", Key.DELETE),
            ("\x1b[5~", Key.PAGE_UP),
            ("\x1b[6~", Key.PAGE_DOWN),
        ],
    )
    def test_csi_sequences(self, sequence: str, expected: Key) -> None:
        assert decode_posix(sequence).key is expected

    def test_the_ss3_prefix_also_works(self) -> None:
        """xterm's application mode uses ``\\x1bO`` rather than ``\\x1b[``."""
        assert decode_posix("\x1bOA").key is Key.UP

    def test_a_lone_escape_is_esc(self) -> None:
        """An ``\\x1b`` with nothing after it is ESC itself."""
        assert decode_posix("\x1b").key is Key.ESC

    def test_an_unmapped_sequence_is_unknown(self) -> None:
        assert decode_posix("\x1b[Z").key is Key.UNKNOWN

    def test_an_empty_sequence_is_unknown(self) -> None:
        assert decode_posix("").key is Key.UNKNOWN

    @pytest.mark.parametrize("raw", ["\r", "\n"])
    def test_enter_has_two_forms(self, raw: str) -> None:
        assert decode_posix(raw).key is Key.ENTER

    def test_backspace_is_del(self) -> None:
        """In raw mode a terminal sends ``\\x7f`` (DEL) for backspace, not ``\\x08``."""
        assert decode_posix("\x7f").key is Key.BACKSPACE

    def test_tab(self) -> None:
        assert decode_posix("\t").key is Key.TAB

    def test_a_printable_char(self) -> None:
        assert decode_posix("q") == KeyPress(Key.CHAR, "q")

    def test_multiple_chars_are_not_a_char(self) -> None:
        """Two printable characters are not one key press. A pasted string must not be
        treated as a single key."""
        assert decode_posix("ab").key is Key.UNKNOWN


# ---------------------------------------------------------------- the selector


class TestSelectReader:
    """The platform name is a parameter, not an ``if sys.platform``."""

    def test_windows_name_yields_the_windows_reader(self) -> None:
        assert isinstance(select_key_reader("nt"), WindowsKeyReader)

    def test_any_other_name_yields_the_posix_reader(self) -> None:
        assert isinstance(select_key_reader("posix"), PosixKeyReader)

    def test_both_branches_are_constructible_here(self) -> None:
        """This is what makes the module developable on Windows.

        If the POSIX reader imported ``termios`` at construction time, this machine would
        raise ``ModuleNotFoundError`` and platform selection would only ever be half covered.
        """
        for name in ("nt", "posix"):
            reader = select_key_reader(name)
            assert isinstance(reader, KeyReader)

    def test_the_default_follows_the_host(self) -> None:
        """Omitting the platform name follows the host. Only the **type** it picked is
        asserted here; no real terminal is touched."""
        reader = select_key_reader()
        expected = WindowsKeyReader if os.name == "nt" else PosixKeyReader
        assert isinstance(reader, expected)


# ---------------------------------------------------------------- Windows reading


class _FakeMsvcrt:
    """A fake ``msvcrt``: yields characters from a script, and ``kbhit`` returns false
    once the script is empty."""

    def __init__(self, script: str = "", *, wide: bool = True) -> None:
        self._script = list(script)
        self._wide = wide
        self.calls = 0

    def kbhit(self) -> bool:
        return bool(self._script)

    def getwch(self) -> str:
        self.calls += 1
        return self._script.pop(0)

    def getch(self) -> bytes:
        self.calls += 1
        return self._script.pop(0).encode("utf-8", "replace")


class TestWindowsReader:
    """The reader itself. Terminal behaviour is simulated with a fake ``msvcrt``."""

    def test_a_printable_key(self) -> None:
        reader = WindowsKeyReader(msvcrt_module=_FakeMsvcrt("a"))
        assert reader.read(timeout=0.1) == KeyPress(Key.CHAR, "a")

    def test_an_arrow_consumes_both_bytes(self) -> None:
        """Both bytes must be consumed together; reading only the prefix leaves ``H`` for the
        next read, which looks like typing the letter h."""
        fake = _FakeMsvcrt("\xe0H")
        reader = WindowsKeyReader(msvcrt_module=fake)

        assert _press(reader).key is Key.UP
        assert fake.calls == 2
        # Nothing should be left in the buffer.
        assert reader.read(timeout=0.01) is None

    def test_the_narrow_api_still_works(self) -> None:
        """Older environments with only ``getch`` (returning bytes) must still work."""
        reader = WindowsKeyReader(msvcrt_module=_FakeMsvcrt("z", wide=False))
        assert reader.read(timeout=0.1) == KeyPress(Key.CHAR, "z")

    def test_no_key_within_the_timeout(self) -> None:
        """No key press returns ``None``, **not** ``UNKNOWN``: the former asks for a
        redraw, the latter does nothing."""
        reader = WindowsKeyReader(msvcrt_module=_FakeMsvcrt(""))
        assert reader.read(timeout=0.02) is None

    def test_a_dangling_prefix_does_not_hang(self) -> None:
        """A half-delivered prefix must be abandoned **within a bound**, or the whole
        UI hangs."""
        fake = _FakeMsvcrt("\xe0")
        reader = WindowsKeyReader(msvcrt_module=fake)

        assert _press(reader, 0.05).key is Key.UNKNOWN

    def test_close_is_a_no_op(self) -> None:
        """``msvcrt`` does not change terminal modes, so there is no state to restore."""
        WindowsKeyReader(msvcrt_module=_FakeMsvcrt("")).close()


# ---------------------------------------------------------------- POSIX reading


class _FakeStdin:
    def __init__(self, fd: int = 7) -> None:
        self._fd = fd

    def fileno(self) -> int:
        return self._fd


class _FakeTermios:
    """Records "raw mode was entered and then restored". A failed restore leaves the
    user's terminal without echo."""

    TCSADRAIN = 1

    def __init__(self) -> None:
        self.saved = object()
        self.get_fd: int | None = None
        self.restored: list[tuple[int, int, object]] = []

    def tcgetattr(self, fd: int) -> object:
        self.get_fd = fd
        return self.saved

    def tcsetattr(self, fd: int, when: int, attrs: object) -> None:
        self.restored.append((fd, when, attrs))


class _FakeTty:
    def __init__(self) -> None:
        self.raw_fds: list[int] = []

    def setraw(self, fd: int) -> None:
        self.raw_fds.append(fd)


class _FakeSelect:
    """Ties "is there data to read" to the byte queue."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def select(
        self, rlist: list[int], wlist: list[int], xlist: list[int], timeout: float
    ) -> tuple[list[int], list[int], list[int]]:
        if self.chunks:
            return rlist, [], []
        return [], [], []


class _FakeOs:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def read(self, fd: int, size: int) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


def _posix_reader(
    script: str,
) -> tuple[PosixKeyReader, _FakeTermios, _FakeTty, _FakeSelect]:
    """Build a POSIX reader that yields bytes from ``script`` one at a time."""
    chunks = [ch.encode("utf-8") for ch in script]
    termios, tty = _FakeTermios(), _FakeTty()
    select_mod = _FakeSelect(chunks)
    modules = PosixModules(
        termios=termios, tty=tty, select=select_mod, os=_FakeOs(chunks)
    )
    reader = PosixKeyReader(stdin=_FakeStdin(), modules=modules)
    return reader, termios, tty, select_mod


class TestPosixReader:
    """Entering and leaving raw mode, and assembling escape sequences."""

    def test_a_printable_key(self) -> None:
        reader, _, _, _ = _posix_reader("q")
        assert reader.read(timeout=0.1) == KeyPress(Key.CHAR, "q")

    def test_raw_mode_is_entered_on_first_read(self) -> None:
        reader, termios, tty, _ = _posix_reader("a")
        reader.read(timeout=0.1)

        assert termios.get_fd == 7
        assert tty.raw_fds == [7]

    def test_an_escape_sequence_is_assembled_byte_by_byte(self) -> None:
        """The three bytes ``\\x1b`` ``[`` ``A`` must combine into one Up arrow."""
        reader, _, _, _ = _posix_reader("\x1b[A")
        assert _press(reader).key is Key.UP

    def test_a_four_byte_sequence_is_assembled(self) -> None:
        """``\\x1b[5~`` is four bytes — stopping at three leaves ``~`` for the next
        read."""
        reader, _, _, _ = _posix_reader("\x1b[5~")
        assert _press(reader).key is Key.PAGE_UP

    def test_a_lone_escape_is_not_held_forever(self) -> None:
        """Pressing ESC must not feel delayed — if no following bytes show up, it is
        ESC."""
        reader, _, _, _ = _posix_reader("\x1b")
        assert _press(reader).key is Key.ESC

    def test_no_input_within_the_timeout(self) -> None:
        reader, _, _, _ = _posix_reader("")
        assert reader.read(timeout=0.02) is None

    def test_raw_mode_is_restored_on_close(self) -> None:
        """Without restoring the mode, the user's terminal loses echo after exiting."""
        reader, termios, _, _ = _posix_reader("a")
        reader.read(timeout=0.1)
        reader.close()

        assert len(termios.restored) == 1
        fd, when, attrs = termios.restored[0]
        assert fd == 7
        assert when == _FakeTermios.TCSADRAIN
        assert attrs is termios.saved

    def test_close_before_any_read_is_harmless(self) -> None:
        reader, termios, _, _ = _posix_reader("")
        reader.close()
        assert termios.restored == []

    def test_close_is_idempotent(self) -> None:
        """Exiting the context manager calls it once more, and a repeated restore must
        not raise."""
        reader, termios, _, _ = _posix_reader("a")
        reader.read(timeout=0.1)
        reader.close()
        reader.close()

        assert len(termios.restored) == 1

    def test_the_context_manager_restores_on_the_way_out(self) -> None:
        """The exception path must restore too — otherwise the terminal is wrecked
        after an error."""
        reader, termios, _, _ = _posix_reader("a")
        with reader:
            pass

        assert len(termios.restored) == 1

    def test_entering_raw_mode_twice_only_saves_once(self) -> None:
        """Every ``read`` calls the enter step; saving repeatedly would overwrite the original
        state with the raw state, so the restore puts raw back and the terminal loses echo."""
        reader, termios, tty, _ = _posix_reader("ab")
        reader.read(timeout=0.1)
        reader.read(timeout=0.1)

        assert tty.raw_fds == [7]
        assert len(termios.restored) == 0  # not closed yet


def test_no_new_runtime_dependency_was_added() -> None:
    """Key reading may use nothing but the standard library.

    Runtime dependencies are just ``typer`` and ``rich``; pulling in ``readchar`` / ``pynput``
    to read one keystroke would make ``pip install mcpdump`` heavier.
    """
    import pathlib

    import mcpdump.ui.keys as module

    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    forbidden = ("readchar", "pynput", "keyboard", "windows-curses", "blessed", "urwid")
    for name in forbidden:
        assert f"import {name}" not in source, (
            f"the key layer pulled in a third-party dependency: {name}"
        )

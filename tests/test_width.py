"""Unit tests for wide-character width, alignment and symbol degradation.

A pure-function layer with no subprocess and no Rich, so it can be covered finely.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

from mcpdump.ui import (
    SYMBOLS,
    center,
    char_width,
    display_width,
    ljust,
    rjust,
    strip_ansi,
    truncate,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestCharWidth:
    def test_ascii_is_one_column(self) -> None:
        for ch in "aZ0 .-_":
            assert char_width(ch) == 1

    def test_cjk_is_two_columns(self) -> None:
        for ch in "中文汉字测试":
            assert char_width(ch) == 2

    def test_fullwidth_punctuation_is_two_columns(self) -> None:
        # Fullwidth punctuation is the easiest to miss: not "Han characters", yet 2 columns wide.
        for ch in "，。！？：；（）【】":
            assert char_width(ch) == 2

    def test_combining_mark_is_zero(self) -> None:
        assert char_width("\u0301") == 0  # COMBINING ACUTE ACCENT

    def test_zero_width_codepoints(self) -> None:
        for cp in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF):
            assert char_width(chr(cp)) == 0

    def test_control_chars_are_zero(self) -> None:
        # The caller splits by line first, so a control char inside a line counts as 0
        assert char_width("\n") == 0
        assert char_width("\t") == 0
        assert char_width("\x00") == 0

    def test_emoji_is_two_columns(self) -> None:
        assert char_width("😀") == 2


class TestDisplayWidth:
    def test_len_is_not_width(self) -> None:
        # This is why the module exists
        assert len("中文") == 2
        assert display_width("中文") == 4

    def test_mixed_script(self) -> None:
        assert display_width("中文abc") == 7

    def test_empty_string(self) -> None:
        assert display_width("") == 0

    def test_ansi_csi_is_stripped(self) -> None:
        assert display_width("\x1b[31mred\x1b[0m") == 3

    def test_ansi_around_cjk(self) -> None:
        assert display_width("\x1b[1m中文\x1b[0m") == 4

    def test_ansi_osc_is_stripped(self) -> None:
        assert display_width("\x1b]0;title\x07ab") == 2


class TestStripAnsi:
    def test_removes_csi(self) -> None:
        assert strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_plain_text_untouched(self) -> None:
        assert strip_ansi("plain") == "plain"


class TestPadding:
    def test_ljust_counts_columns_not_chars(self) -> None:
        assert ljust("中文", 6) == "中文  "
        assert display_width(ljust("中文", 6)) == 6

    def test_rjust(self) -> None:
        assert rjust("中文", 6) == "  中文"
        assert display_width(rjust("中文", 6)) == 6

    def test_center_is_exact(self) -> None:
        result = center("中文", 7)
        assert display_width(result) == 7
        assert result.strip() == "中文"

    def test_padding_never_truncates(self) -> None:
        # Returned as is when over budget; truncating is the caller's call
        assert ljust("中文中文", 4) == "中文中文"

    def test_alignment_is_consistent(self) -> None:
        # Mixed-script keys padded to the same width must display equally wide
        keys = ["name", "协议版本", "传输", "tools"]
        assert {display_width(ljust(k, 10)) for k in keys} == {10}


class TestTruncate:
    def test_short_text_untouched(self) -> None:
        assert truncate("abc", 10) == "abc"

    def test_exact_fit_untouched(self) -> None:
        assert truncate("abcd", 4) == "abcd"

    def test_ascii_truncation(self) -> None:
        assert truncate("abcdef", 4) == "abc…"

    def test_ellipsis_counts_one_column(self) -> None:
        # The key point: the ellipsis takes one column, so it comes out of the budget
        assert display_width(truncate("abcdef", 4)) == 4

    def test_never_exceeds_budget_for_wide_chars(self) -> None:
        result = truncate("中文中文中文", 5)
        assert display_width(result) <= 5
        assert result.endswith("…")

    def test_never_splits_a_wide_char(self) -> None:
        # Budget 3: room for one wide character plus the ellipsis
        assert truncate("中文中文", 3) == "中…"

    def test_zero_width_returns_empty(self) -> None:
        assert truncate("abc", 0) == ""

    def test_width_one_returns_ellipsis(self) -> None:
        assert truncate("abc", 1) == "…"


def _run_python(code: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run a small snippet in a subprocess to check an env var's effect at import time."""
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=ROOT,
        timeout=60,
    )


class TestSymbols:
    def test_glyphs_are_unique(self) -> None:
        # The rule: one symbol keeps the same meaning across every command.
        # Two keys sharing a glyph would break that rule.
        glyphs = list(SYMBOLS.as_dict().values())
        assert len(glyphs) == len(set(glyphs))

    def test_unicode_by_default(self, mcpdump_env: dict[str, str]) -> None:
        proc = _run_python(
            "from mcpdump.ui import SYMBOLS; print(SYMBOLS.request, SYMBOLS.selected)", mcpdump_env
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "→ ▸"

    def test_ascii_degradation(self, mcpdump_env: dict[str, str]) -> None:
        env = dict(mcpdump_env)
        env["MCPDUMP_ASCII"] = "1"
        proc = _run_python(
            "from mcpdump.ui import SYMBOLS; print(SYMBOLS.request, SYMBOLS.selected)", env
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "-> >"

    def test_truthy_env_values(self, mcpdump_env: dict[str, str]) -> None:
        for value in ("1", "true", "TRUE", "yes", "on"):
            env = dict(mcpdump_env)
            env["MCPDUMP_ASCII"] = value
            proc = _run_python("from mcpdump.ui import use_ascii; print(use_ascii())", env)
            assert proc.stdout.strip() == "True", f"MCPDUMP_ASCII={value!r} should be read as true"

    def test_falsy_env_values(self, mcpdump_env: dict[str, str]) -> None:
        for value in ("", "0", "no", "off", "anything"):
            env = dict(mcpdump_env)
            env["MCPDUMP_ASCII"] = value
            proc = _run_python("from mcpdump.ui import use_ascii; print(use_ascii())", env)
            assert proc.stdout.strip() == "False", (
                f"MCPDUMP_ASCII={value!r} should be read as false"
            )

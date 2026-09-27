"""Unit tests for the three-part skeleton and the alignment primitives.

``layout`` is a plain-text layer, so tests assert on strings directly and are unaffected
by the Rich version.
"""

from __future__ import annotations

from mcpdump.ui import HINT_MARK, display_width
from mcpdump.ui.layout import (
    bullet_lines,
    hint_lines,
    indent_lines,
    join_blocks,
    key_value_lines,
    section_lines,
)


class TestKeyValueLines:
    def test_values_align_across_cjk_and_ascii_keys(self) -> None:
        # Core guarantee: with CJK and ASCII keys mixed, values start in the same column
        lines = key_value_lines([("传输", "stdio"), ("协议版本", "2025-06-18")])
        assert len(lines) == 2
        start0 = display_width(lines[0][: lines[0].index("stdio")])
        start1 = display_width(lines[1][: lines[1].index("2025-06-18")])
        assert start0 == start1

    def test_explicit_key_width(self) -> None:
        lines = key_value_lines([("a", "1")], key_width=6)
        assert lines[0] == "a       1"
        assert display_width(lines[0][: lines[0].index("1")]) == 8  # 6 + the default gap of 2

    def test_empty_input(self) -> None:
        assert key_value_lines([]) == []

    def test_none_value_becomes_empty(self) -> None:
        assert key_value_lines([("a", None)], key_width=1) == ["a  "]

    def test_multiline_value_hangs_aligned(self) -> None:
        lines = key_value_lines([("k", "l1\nl2")], key_width=3)
        assert len(lines) == 2
        assert lines[1] == " " * 5 + "l2"

    def test_cjk_key_width_uses_columns(self) -> None:
        lines = key_value_lines([("命令", "python -m mcpdump.demo")])
        assert display_width(lines[0][: lines[0].index("python")]) == 6

    def test_custom_gap(self) -> None:
        lines = key_value_lines([("a", "1")], key_width=1, gap=4)
        assert lines[0] == "a    1"


class TestSectionLines:
    def test_rule_matches_title_display_width(self) -> None:
        lines = section_lines("身份", ["a"])
        assert lines[0] == "身份"
        assert display_width(lines[1]) == 4

    def test_rule_is_ascii_width(self) -> None:
        lines = section_lines("abc", [])
        assert lines[1] == "───"

    def test_no_rule(self) -> None:
        assert section_lines("t", ["a"], rule=None) == ["t", "a"]

    def test_body_is_preserved(self) -> None:
        assert section_lines("t", ["a", "b"], rule=None) == ["t", "a", "b"]


class TestHintLines:
    def test_prefixes_each_hint(self) -> None:
        assert hint_lines(["a", "b"]) == [f"{HINT_MARK} a", f"{HINT_MARK} b"]

    def test_skips_empty_hints(self) -> None:
        # Callers build hints conditionally; that must not leave a lone marker line
        assert hint_lines(["a", "", "b"]) == [f"{HINT_MARK} a", f"{HINT_MARK} b"]

    def test_custom_mark(self) -> None:
        assert hint_lines(["a"], mark="*") == ["* a"]

    def test_all_empty(self) -> None:
        assert hint_lines(["", ""]) == []


class TestBulletLines:
    def test_truncates_to_display_width(self) -> None:
        lines = bullet_lines(["中文中文中文中文"], max_width=8)
        assert display_width(lines[0]) <= 8

    def test_indent(self) -> None:
        assert bullet_lines(["a"], indent=1)[0].startswith("  ")

    def test_short_item_untouched(self) -> None:
        assert bullet_lines(["a"], max_width=40) == [f"{HINT_MARK} a"]


class TestJoinBlocks:
    def test_inserts_blank_line(self) -> None:
        assert join_blocks(["a"], ["b"]) == ["a", "", "b"]

    def test_skips_empty_blocks(self) -> None:
        assert join_blocks(["a"], [], ["b"]) == ["a", "", "b"]

    def test_custom_gap(self) -> None:
        assert join_blocks(["a"], ["b"], gap=2) == ["a", "", "", "b"]

    def test_single_block(self) -> None:
        assert join_blocks(["a", "b"]) == ["a", "b"]

    def test_no_blocks(self) -> None:
        assert join_blocks() == []


class TestIndentLines:
    def test_indents_non_empty_only(self) -> None:
        # Blank lines are not indented, so no trailing whitespace appears
        assert indent_lines(["a", ""], 1) == ["  a", ""]

    def test_two_levels(self) -> None:
        assert indent_lines(["a"], 2) == ["    a"]

    def test_zero_level(self) -> None:
        assert indent_lines(["a"], 0) == ["a"]

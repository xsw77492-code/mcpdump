"""Tests for ``ui/tui_view.py``: state → one screen of rows.

The geometry tests all assert the row count equals ``height`` and every row's
``display_width`` equals ``width``. Counting with ``len`` breaks on CJK, where a
full-width character is two columns.
"""

from __future__ import annotations

import ast
import pathlib
import re
from dataclasses import replace

import pytest

from mcpdump import i18n
from mcpdump.core.session import ServerInfo
from mcpdump.ui import (
    MIN_HEIGHT,
    MIN_WIDTH,
    OUTLIER_MIN_DELTA_MS,
    OUTLIER_RATIO,
    CallRecord,
    Key,
    KeyPress,
    Mode,
    Pane,
    ToolEntry,
    TuiState,
    apply_key,
    build_view,
    display_width,
    initial_state,
    is_outlier,
    layout_of,
    push_call,
    push_frame,
    request_frame,
    response_frame,
    tui_view,
    water_bar,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULE = ROOT / "src" / "mcpdump" / "ui" / "tui_view.py"

_BAR_GLYPHS = frozenset("▏▎▍▌▋▊▉█")

#: Common terminal sizes. The two ``MIN_*`` entries sit on the boundary, easiest to miss.
SIZES = [
    (MIN_WIDTH, MIN_HEIGHT),
    (30, 10),
    (40, 12),
    (56, 18),
    (72, 20),
    (80, 24),
    (100, 26),
    (120, 40),
    (160, 50),
    (200, 60),
]


# ---------------------------------------------------------------- construction


def _tool(name: str, description: str = "", required: tuple[str, ...] = ()) -> ToolEntry:
    return ToolEntry.from_payload(
        {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": {key: {"type": "string"} for key in required},
                "required": list(required),
            },
        }
    )


def _server() -> ServerInfo:
    return ServerInfo(
        name="echo-server",
        version="0.1.0",
        protocol_version="2025-06-18",
        capabilities={"tools": {}, "resources": {}},
    )


def _state(
    *,
    tools: tuple[ToolEntry, ...] = (),
    frames: int = 0,
    calls: tuple[CallRecord, ...] = (),
    server: bool = True,
) -> TuiState:
    state = initial_state(
        server=_server() if server else None,
        transport="Streamable HTTP" if server else "",
        tools=tools,
    )
    for index in range(frames):
        state = push_frame(state, request_frame("tools/call", f'{{"n":{index}}}'))
        state = push_frame(
            state, response_frame("tools/call", f'{{"r":{index}}}', elapsed_ms=10.0)
        )
    for record in calls:
        state = push_call(state, record)
    return state


def _press(state: TuiState, *presses: KeyPress) -> TuiState:
    for press in presses:
        state = apply_key(state, press).state
    return state


def _char(value: str) -> KeyPress:
    return KeyPress(Key.CHAR, value)


def _clear_args(state: TuiState) -> TuiState:
    """Clear the args line: go home, then delete forward all the way."""
    return _press(state, KeyPress(Key.HOME), *([KeyPress(Key.DELETE)] * 40))


def _assert_geometry(state: TuiState, width: int, height: int) -> list[str]:
    """The core invariant: row count == height and each row's display_width == width."""
    lines = build_view(state, width=width, height=height)
    assert len(lines) == height, f"line count {len(lines)} != {height}"
    for index, line in enumerate(lines):
        measured = display_width(line.plain)
        assert measured == width, (
            f"{width}x{height} row {index} width {measured} != {width}: {line.plain!r}"
        )
    return [line.plain for line in lines]


def _bar_len(text: str) -> int:
    return sum(1 for ch in text if ch in _BAR_GLYPHS)


def _rows_containing(lines: list[str], needle: str) -> list[int]:
    return [index for index, line in enumerate(lines) if needle in line]


def _with_pane(state: TuiState, pane: Pane) -> TuiState:
    return replace(state, pane=pane)


# ---------------------------------------------------------------- budget


class TestLayout:
    def test_the_columns_add_up_to_the_terminal_width(self) -> None:
        for width in range(MIN_WIDTH, 220):
            lay = layout_of(width, 30, has_calls=False)
            assert lay.left_w + lay.right_w + 3 == width, width
            assert lay.left_w > 0 and lay.right_w > 0

    def test_the_wire_pane_keeps_a_usable_share(self) -> None:
        """Squeezing the wire pane away to make the list prettier is a bad trade."""
        for width in range(MIN_WIDTH, 220):
            lay = layout_of(width, 30, has_calls=False)
            assert lay.right_w >= (width - 3) * 4 // 10, (width, lay)

    def test_the_rows_add_up_to_the_terminal_height(self) -> None:
        for height in range(MIN_HEIGHT, 60):
            for has_calls in (False, True):
                lay = layout_of(100, height, has_calls=has_calls)
                # top frame + identity + join + body + timeline block + bottom frame + status bar
                assert lay.body_rows >= 1, (height, has_calls)
                assert 3 + lay.body_rows + lay.timeline_block + 1 + 1 == height

    def test_the_timeline_block_stays_within_its_bounds(self) -> None:
        for height in range(MIN_HEIGHT, 80):
            block = layout_of(100, height, has_calls=True).timeline_block
            assert block == 0 or 3 <= block <= 6, height

    def test_the_timeline_only_appears_when_there_are_calls(self) -> None:
        assert layout_of(100, 30, has_calls=False).timeline_block == 0
        assert layout_of(100, 30, has_calls=True).timeline_block > 0

    def test_the_timeline_is_dropped_rather_than_squeezing_the_body(self) -> None:
        """Short on rows: drop the timeline, never squeeze the body to 0."""
        for height in range(MIN_HEIGHT, 14):
            assert layout_of(100, height, has_calls=True).body_rows >= 1

    def test_small_terminals_are_reported_as_degraded(self) -> None:
        assert layout_of(MIN_WIDTH - 1, 30, has_calls=False).degraded
        assert layout_of(100, MIN_HEIGHT - 1, has_calls=False).degraded
        assert not layout_of(MIN_WIDTH, MIN_HEIGHT, has_calls=False).degraded

    def test_a_wide_terminal_caps_the_tool_pane(self) -> None:
        """On a wide terminal, letting the tools pane widen squeezes the wire pane into a slit."""
        assert layout_of(400, 60, has_calls=False).left_w == 40


# ---------------------------------------------------------------- geometry


class TestGeometry:
    """Geometry invariants, checked with no terminal at all."""

    def test_the_empty_state_fits_every_size(self) -> None:
        for width, height in SIZES:
            _assert_geometry(_state(), width, height)

    def test_a_state_without_a_server_fits_every_size(self) -> None:
        for width, height in SIZES:
            _assert_geometry(_state(server=False), width, height)

    def test_wide_characters_do_not_break_the_frame(self) -> None:
        """A line with CJK comes up short when counted with ``len``; the borders shift at once."""
        tools = (
            _tool("中文工具", "这是一个很长的中文描述，用来验证截断与补齐"),
            _tool("echo", "回显输入的中文文本"),
            _tool("混合mixed名字", "中英混排 description 混排"),
        )
        server = ServerInfo(
            name="中文服务端",
            version="1.0.0",
            title="标题也含中文",
            protocol_version="2025-06-18",
            capabilities={"tools": {}},
        )
        state = initial_state(server=server, transport="Streamable HTTP", tools=tools)
        state = push_frame(
            state, request_frame("tools/call", '{"text":"你好，世界，这是一段中文"}')
        )
        for width, height in SIZES:
            _assert_geometry(state, width, height)

    def test_overlong_lines_are_truncated_not_wrapped(self) -> None:
        state = _state(tools=(_tool("t", "x" * 400),), frames=1)
        for width, height in SIZES:
            lines = _assert_geometry(state, width, height)
            assert all("\n" not in line for line in lines)

    def test_every_size_holds_for_each_focused_pane(self) -> None:
        state = _state(
            tools=(_tool("echo"), _tool("add"), _tool("boom")),
            frames=6,
            calls=(
                CallRecord("echo", 12.4, True),
                CallRecord("add", 31.2, True),
                CallRecord("boom", 30000.0, False, "timeout after 30s"),
                CallRecord("echo", 14.1, True),
            ),
        )
        for width, height in SIZES:
            for pane in (Pane.TOOLS, Pane.WIRE, Pane.TIMELINE):
                _assert_geometry(_with_pane(state, pane), width, height)

    def test_every_size_holds_in_all_three_modes(self) -> None:
        base = _state(tools=(_tool("echo", required=("text",)),), frames=2)
        modes = {
            Mode.BROWSE: base,
            Mode.FILTER: _press(base, _char("/"), _char("e")),
            Mode.ARGS: _press(base, KeyPress(Key.ENTER)),
        }
        for mode, state in modes.items():
            assert state.mode is mode, mode
            for width, height in SIZES:
                _assert_geometry(state, width, height)

    def test_an_expanded_frame_fits_every_size(self) -> None:
        state = _state(frames=3)
        state = _press(state, KeyPress(Key.TAB), KeyPress(Key.ENTER))
        assert state.expanded_seq is not None
        for width, height in SIZES:
            _assert_geometry(state, width, height)

    def test_a_scrolled_view_fits_every_size(self) -> None:
        state = _state(tools=(_tool("a"),), frames=20)
        state = _press(state, KeyPress(Key.TAB), KeyPress(Key.UP), KeyPress(Key.UP))
        for width, height in SIZES:
            _assert_geometry(state, width, height)

    def test_zero_and_negative_sizes_do_not_explode(self) -> None:
        assert build_view(_state(), width=0, height=0) == []
        assert build_view(_state(), width=10, height=0) == []
        narrow = build_view(_state(), width=0, height=5)
        assert len(narrow) == 5
        assert all(line.plain == "" for line in narrow)

    def test_frame_rows_are_drawn_with_borders(self) -> None:
        """Width alone is not enough: a cropped right border measures the same too."""
        state = _state(
            tools=(_tool("echo"),), frames=2, calls=(CallRecord("echo", 5.0, True),)
        )
        lines = _assert_geometry(state, 100, 30)
        lay = layout_of(100, 30, has_calls=True)
        timeline_top = 3 + lay.body_rows

        for index in (0, 2, timeline_top, len(lines) - 2):
            assert lines[index][0] in "┌├└", (index, lines[index])
            assert lines[index][-1] in "┐┤┘", (index, lines[index])
        for index in range(3, timeline_top):
            assert lines[index][0] == "│" and lines[index][-1] == "│", (index, lines[index])


# ---------------------------------------------------------------- water bar


class TestWaterBar:
    def test_the_scale_maximum_fills_the_track(self) -> None:
        assert water_bar(100.0, scale_ms=100.0, cells=10) == "█" * 10

    def test_half_the_scale_is_half_the_track(self) -> None:
        assert water_bar(50.0, scale_ms=100.0, cells=10) == "█" * 5

    def test_sub_cell_precision_uses_the_partial_glyphs(self) -> None:
        """Whole-cell scaling makes 12ms and 13ms look equal; this bar exists to separate them."""
        assert water_bar(1.0, scale_ms=100.0, cells=10) == "▏"

    def test_a_successful_call_never_gets_an_empty_bar(self) -> None:
        """A zero-length bar and a failed ``✗`` look identical, but they are different things."""
        assert water_bar(0.001, scale_ms=10_000.0, cells=40) == "▏"

    def test_the_bar_never_exceeds_its_cell_budget(self) -> None:
        for value in (0.0, 0.1, 1.0, 50.0, 99.9, 100.0, 1e9):
            for cells in (1, 2, 7, 40):
                assert len(water_bar(value, scale_ms=100.0, cells=cells)) <= cells

    def test_a_meaningless_scale_yields_nothing(self) -> None:
        assert water_bar(10.0, scale_ms=0.0, cells=10) == ""
        assert water_bar(10.0, scale_ms=-1.0, cells=10) == ""
        assert water_bar(10.0, scale_ms=100.0, cells=0) == ""
        assert water_bar(0.0, scale_ms=100.0, cells=10) == ""

    def test_ascii_mode_degrades_to_whole_cells(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCPDUMP_ASCII", "1")
        assert water_bar(50.0, scale_ms=100.0, cells=10) == "#" * 5
        assert water_bar(0.001, scale_ms=10_000.0, cells=40) == "#"


# ---------------------------------------------------------------- outlier test


class TestOutlier:
    def test_both_thresholds_must_hold_together(self) -> None:
        baseline = 100.0
        assert is_outlier(baseline * OUTLIER_RATIO + OUTLIER_MIN_DELTA_MS, baseline)

    def test_a_large_ratio_on_a_tiny_baseline_is_not_an_outlier(self) -> None:
        """2ms becoming 4ms is a 2x change, but it means nothing to the user."""
        assert not is_outlier(4.0, 2.0)

    def test_a_large_delta_on_a_slow_baseline_is_not_an_outlier(self) -> None:
        """3 seconds becoming 3.1 seconds costs 100ms more, but nobody cares."""
        assert not is_outlier(3100.0, 3000.0)

    def test_a_missing_baseline_disables_the_check(self) -> None:
        assert not is_outlier(5000.0, 0.0)


# ---------------------------------------------------------------- timeline


class TestTimeline:
    def test_the_summary_uses_the_median_not_the_mean(self) -> None:
        """One 30-second timeout can inflate the mean tenfold, and then every call "looks fast"."""
        state = _state(
            calls=(
                CallRecord("a", 10.0, True),
                CallRecord("b", 12.0, True),
                CallRecord("c", 14.0, True),
                CallRecord("boom", 30_000.0, False, "timeout"),
            )
        )
        lines = _assert_geometry(state, 100, 30)
        summary = next(line for line in lines if "Timeline" in line)
        assert "p50 12.0 ms" in summary
        assert "max 14.0 ms" in summary

    def test_the_summary_says_so_when_nothing_succeeded(self) -> None:
        state = _state(calls=(CallRecord("boom", 30_000.0, False, "timeout"),))
        lines = _assert_geometry(state, 100, 30)
        assert any("none succeeded" in line for line in lines)

    def test_a_failed_call_shows_its_reason_instead_of_a_bar(self) -> None:
        """A length would suggest it was slow, but "slow" and "failed" need separate triage."""
        state = _state(
            calls=(
                CallRecord("ok", 20.0, True),
                CallRecord("boom", 30_000.0, False, "timeout after 30s"),
            )
        )
        lines = _assert_geometry(state, 100, 30)
        row = next(line for line in lines if "boom" in line)
        assert "timeout after 30s" in row
        assert _bar_len(row) == 0
        assert "—" in row

    def test_a_slow_call_gets_the_warning_mark(self) -> None:
        calls = tuple(CallRecord(f"t{i}", 10.0, True) for i in range(6))
        state = _state(calls=(*calls, CallRecord("slow", 900.0, True)))
        lines = _assert_geometry(state, 100, 30)
        row = next(line for line in lines if "slow" in line)
        assert "⚠" in row

    def test_a_normal_call_gets_no_warning_mark(self) -> None:
        state = _state(calls=tuple(CallRecord(f"t{i}", 10.0, True) for i in range(4)))
        lines = _assert_geometry(state, 100, 30)
        assert all("⚠" not in line for line in lines)

    def test_only_the_most_recent_calls_are_shown(self) -> None:
        calls = tuple(CallRecord(f"call{i:02d}", 10.0 + i, True) for i in range(40))
        lines = _assert_geometry(_state(calls=calls), 100, 30)
        shown = [line for line in lines if re.search(r"call\d\d", line)]
        assert len(shown) == layout_of(100, 30, has_calls=True).call_rows
        joined = "\n".join(lines)
        assert "call39" in joined
        assert "call00" not in joined

    def test_calls_are_ordered_oldest_first(self) -> None:
        state = _state(
            calls=(CallRecord("older", 10.0, True), CallRecord("newer", 20.0, True))
        )
        lines = _assert_geometry(state, 100, 30)
        older = next(i for i, line in enumerate(lines) if "older" in line)
        newer = next(i for i, line in enumerate(lines) if "newer" in line)
        assert older < newer

    def test_bars_scale_to_the_visible_window(self) -> None:
        """Bar lengths normalize to the window's maximum; otherwise one timeout flattens them."""
        state = _state(
            calls=(CallRecord("half", 50.0, True), CallRecord("full", 100.0, True))
        )
        lines = _assert_geometry(state, 100, 30)
        half = next(line for line in lines if "half" in line)
        full = next(line for line in lines if "full" in line)
        assert _bar_len(half) == pytest.approx(_bar_len(full) / 2, abs=2)

    def test_the_longest_visible_call_fills_its_track(self) -> None:
        state = _state(
            calls=tuple(CallRecord(f"t{i}", 5.0 * (i + 1), True) for i in range(4))
        )
        lines = _assert_geometry(state, 100, 30)
        bars = {
            line.split()[0]: _bar_len(line) for line in lines if _bar_len(line) > 0
        }
        assert len(bars) == 4
        assert bars["t3"] == max(bars.values())
        assert len(set(bars.values())) == 4

    def test_the_timeline_is_absent_until_the_first_call(self) -> None:
        lines = _assert_geometry(_state(tools=(_tool("echo"),)), 100, 30)
        assert not any("Timeline" in line for line in lines)


# ---------------------------------------------------------------- tools pane


class TestToolsPane:
    def test_the_selected_tool_is_marked(self) -> None:
        lines = _assert_geometry(_state(tools=(_tool("alpha"), _tool("beta"))), 100, 30)
        assert "▸" in next(line for line in lines if "alpha" in line)
        assert "▸" not in next(line for line in lines if "beta" in line)

    def test_the_list_grows_from_the_top(self) -> None:
        """The list grows from the top; bottom-aligned tools float mid-air and look broken."""
        lines = _assert_geometry(_state(tools=(_tool("alpha"), _tool("beta"))), 100, 30)
        assert "alpha" in lines[3]
        assert not any("alpha" in line for line in lines[10:])

    def test_the_description_is_shown_next_to_the_name(self) -> None:
        state = _state(tools=(_tool("echo", "Echo back the text"),))
        lines = _assert_geometry(state, 120, 30)
        assert any("Echo back the text" in line for line in lines)

    def test_filtering_narrows_the_list(self) -> None:
        state = _state(tools=(_tool("alpha"), _tool("beta"), _tool("gamma")))
        state = _press(state, _char("/"), _char("b"))
        lines = _assert_geometry(state, 100, 30)
        assert any("beta" in line for line in lines)
        assert not any("alpha" in line for line in lines)
        assert any("FILTER" in line for line in lines)

    def test_an_empty_result_blames_the_filter(self) -> None:
        state = _press(_state(tools=(_tool("alpha"),)), _char("/"), _char("z"))
        state = _press(state, _char("z"), _char("z"))
        lines = _assert_geometry(state, 100, 30)
        assert any("no tool matches the filter" in line for line in lines)

    def test_a_server_without_tools_says_so(self) -> None:
        lines = _assert_geometry(_state(), 100, 30)
        assert any("exposes no tools" in line for line in lines)

    def test_the_selection_stays_inside_the_window(self) -> None:
        state = _state(tools=tuple(_tool(f"tool{i:02d}") for i in range(40)))
        state = _press(state, *([KeyPress(Key.DOWN)] * 39))
        lines = _assert_geometry(state, 100, 20)
        assert any("▸" in line and "tool39" in line for line in lines)


# ---------------------------------------------------------------- wire pane


class TestWirePane:
    def test_the_newest_frame_sits_at_the_bottom(self) -> None:
        """The newest entry sits at the pane's bottom, so scrolling back stays stable."""
        lines = _assert_geometry(_state(frames=1), 100, 30)
        bottom = 3 + layout_of(100, 30, has_calls=False).body_rows - 1
        assert '{"r":0}' in lines[bottom]
        assert "← tools/call" in lines[bottom - 1]

    def test_the_cursor_marks_the_newest_frame_by_default(self) -> None:
        lines = _assert_geometry(_state(frames=2), 100, 30)
        marked = _rows_containing(lines, "▸")
        assert marked, lines
        assert "← tools/call" in lines[marked[0]]

    def test_scrolling_back_moves_the_cursor_up(self) -> None:
        state = _state(frames=8)
        before = _rows_containing(_assert_geometry(state, 100, 30), "▸")[0]
        scrolled = _press(state, KeyPress(Key.TAB), KeyPress(Key.UP))
        after = _rows_containing(_assert_geometry(scrolled, 100, 30), "▸")[0]
        assert after < before

    def test_scrolling_back_shows_the_position(self) -> None:
        """Scrolling without position feedback is scrolling blind."""
        state = _press(_state(frames=8), KeyPress(Key.TAB), KeyPress(Key.UP))
        lines = _assert_geometry(state, 100, 30)
        assert any("Wire · 15/16" in line for line in lines)

    def test_hidden_frames_are_announced(self) -> None:
        lines = _assert_geometry(_state(frames=40), 100, 20)
        assert any("older" in line for line in lines)

    def test_an_expanded_frame_shows_the_whole_payload(self) -> None:
        """Expanded draws only this frame; the other frames are hidden."""
        state = _state(frames=1)
        state = _press(state, KeyPress(Key.TAB), KeyPress(Key.ENTER))
        assert state.expanded_seq is not None
        joined = "\n".join(_assert_geometry(state, 100, 30))
        assert '{"r":0}' in joined
        assert '{"n":0}' not in joined

    def test_escape_collapses_the_expanded_frame(self) -> None:
        state = _state(frames=2)
        state = _press(state, KeyPress(Key.TAB), KeyPress(Key.ENTER), KeyPress(Key.ESC))
        assert state.expanded_seq is None
        joined = "\n".join(_assert_geometry(state, 100, 30))
        assert '{"n":1}' in joined and '{"r":1}' in joined

    def test_the_empty_state_explains_what_to_do(self) -> None:
        lines = _assert_geometry(_state(tools=(_tool("echo"),)), 100, 30)
        assert any("press" in line and "to call it" in line for line in lines)


# ---------------------------------------------------------------- status bar


class TestStatusBar:
    def test_the_mode_label_follows_the_mode(self) -> None:
        base = _state(tools=(_tool("echo"),))
        assert any("BROWSE" in line for line in _assert_geometry(base, 100, 30))
        filtered = _press(base, _char("/"))
        assert any("FILTER" in line for line in _assert_geometry(filtered, 100, 30))

    def test_the_browse_hint_lists_the_single_keys(self) -> None:
        status = _assert_geometry(_state(), 120, 30)[-1]
        for token in ("tab", "⏎", "/", "q"):
            assert token in status, status

    def test_the_args_editor_shows_the_buffer_and_a_cursor(self) -> None:
        state = _press(_state(tools=(_tool("echo", required=("text",)),)), KeyPress(Key.ENTER))
        status = _assert_geometry(state, 120, 30)[-1]
        assert "ARGS" in status
        assert '"text"' in status
        assert "▌" in status

    def test_the_args_cursor_stays_visible_in_a_long_buffer(self) -> None:
        state = _press(_state(tools=(_tool("echo", required=("text",)),)), KeyPress(Key.ENTER))
        state = _press(state, *([_char("x")] * 80))
        assert "▌" in _assert_geometry(state, 60, 20)[-1]

    def test_a_bad_json_buffer_is_reported(self) -> None:
        state = _press(_state(tools=(_tool("echo", required=("text",)),)), KeyPress(Key.ENTER))
        state = _press(_clear_args(state), _char("{"), KeyPress(Key.ENTER))
        lines = _assert_geometry(state, 120, 30)
        assert any("not valid JSON" in line for line in lines)

    def test_a_non_object_buffer_is_reported(self) -> None:
        state = _press(_state(tools=(_tool("echo", required=("text",)),)), KeyPress(Key.ENTER))
        state = _press(_clear_args(state), _char("["), _char("]"), KeyPress(Key.ENTER))
        lines = _assert_geometry(state, 120, 30)
        assert any("must be a JSON object" in line for line in lines)

    def test_a_missing_required_parameter_is_named(self) -> None:
        state = _press(_state(tools=(_tool("echo", required=("text",)),)), KeyPress(Key.ENTER))
        state = _press(_clear_args(state), _char("{"), _char("}"), KeyPress(Key.ENTER))
        lines = _assert_geometry(state, 120, 30)
        assert any("missing required parameter: text" in line for line in lines)

    def test_a_running_call_is_announced(self) -> None:
        state = _press(_state(tools=(_tool("echo"),)), KeyPress(Key.ENTER))
        assert state.busy
        lines = _assert_geometry(state, 100, 30)
        assert any("running…" in line for line in lines)

    def test_a_multiline_error_is_collapsed_to_one_line(self) -> None:
        """Wrapping would push the status bar to two lines and knock the layout out of place."""
        state = replace(_state(), error_text="boom\n  at line 2\n  at line 3")
        lines = _assert_geometry(state, 100, 30)
        assert any("boom at line 2" in line for line in lines)


# ---------------------------------------------------------------- degraded


class TestDegraded:
    def test_a_tiny_terminal_still_shows_the_mode(self) -> None:
        for width in range(0, MIN_WIDTH):
            for height in (1, 2, 5, MIN_HEIGHT - 1):
                lines = _assert_geometry(_state(), width, height)
                assert "BROWSE" in lines[-1] or width < 8, (width, height)

    def test_a_tiny_terminal_leaves_the_rest_blank(self) -> None:
        lines = _assert_geometry(_state(), 20, 5)
        assert lines[0].strip() == ""
        assert lines[-1].strip() != ""


# ---------------------------------------------------------------- i18n


class TestLocalisation:
    def test_every_size_renders_in_both_languages(self) -> None:
        state = _state(
            tools=(_tool("echo", "回显", required=("text",)),),
            frames=3,
            calls=(CallRecord("echo", 12.0, True),),
        )
        for language in ("en", "zh"):
            i18n.set_language(language)
            for width, height in SIZES:
                _assert_geometry(state, width, height)

    def test_the_chinese_view_is_actually_chinese(self) -> None:
        i18n.set_language("zh")
        lines = _assert_geometry(_state(tools=(_tool("echo"),)), 100, 30)
        assert any("服务端" in line for line in lines)
        assert any("浏览" in line for line in lines)

    def test_the_ascii_mode_fits_every_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MCPDUMP_ASCII", "1")
        state = _state(
            tools=(_tool("echo"),), frames=2, calls=(CallRecord("e", 5.0, True),)
        )
        for width, height in SIZES:
            lines = _assert_geometry(state, width, height)
        assert any("+" in line for line in lines)


# ---------------------------------------------------------------- source hygiene


def _literal_strings(path: pathlib.Path) -> list[tuple[int, str]]:
    """Every string literal in the module, excluding docstrings.

    Docstrings are excluded because the project documents itself in Chinese; this test
    only cares about Chinese that reaches user-visible output.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _has_cjk(text: str) -> bool:
    return any(
        "\u3000" <= ch <= "\u303f"
        or "\u4e00" <= ch <= "\u9fff"
        or "\uff00" <= ch <= "\uffef"
        for ch in text
    )


def test_the_view_module_hardcodes_no_translatable_text() -> None:
    """User-visible strings must go through ``i18n.t()``.

    Parses the AST so it can tell a docstring mention from a string really written into
    the output; only CJK is checked, since the glyphs themselves are not copy.
    """
    offenders = [
        f"{MODULE.name}:{line} {value!r}"
        for line, value in _literal_strings(MODULE)
        if _has_cjk(value)
    ]
    assert not offenders, "hard-coded copy should go through i18n.t():\n" + "\n".join(offenders)


def test_the_view_module_exports_what_it_promises() -> None:
    for name in tui_view.__all__:
        assert hasattr(tui_view, name), name

"""Tests for ``ui/tui_state.py``: key sequences → final state.

All interaction logic lives in this pure state machine, so "which key was pressed, what
the screen becomes" is covered here with no terminal and no server.

The key constructors: ``_press("q")`` for character keys, ``_key(Key.UP)`` for function
keys.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import replace
from typing import Any

from mcpdump.ui import (
    MAX_FRAMES,
    ArgsProblem,
    CallRecord,
    Key,
    KeyPress,
    Mode,
    Pane,
    PendingCall,
    ToolEntry,
    TuiAction,
    TuiState,
    apply_key,
    args_skeleton,
    initial_state,
    push_call,
    push_frame,
    request_frame,
    set_server,
    set_tools,
    settle,
    visible_tools,
)
from mcpdump.ui.tui_state import _first_value_start


def _key(key: Key) -> KeyPress:
    return KeyPress(key)


def _press(char: str) -> KeyPress:
    return KeyPress(Key.CHAR, char)


def _run(state: TuiState, *presses: KeyPress) -> TuiState:
    """Feed a run of key presses and return the final state. Actions are ignored —
    tests that need an action take it separately."""
    for press in presses:
        state = apply_key(state, press).state
    return state


def _tool(name: str, *, required: tuple[str, ...] = (), **extra: Any) -> ToolEntry:
    props = {key: {"type": "string"} for key in required}
    payload: dict[str, Any] = {
        "name": name,
        "description": extra.pop("description", ""),
        "inputSchema": {"type": "object", "properties": props, "required": list(required)},
    }
    payload.update(extra)
    return ToolEntry.from_payload(payload)


def _state_with_tools(*names: str) -> TuiState:
    return set_tools(initial_state(), tuple(_tool(name) for name in names))


def _clear_args(state: TuiState) -> TuiState:
    """Clear the args line: go to the start, then delete by length, to build the
    "user wiped the skeleton clean" case where missing args must be caught locally.
    """
    state = _run(state, _key(Key.HOME))
    return _run(state, *[_key(Key.DELETE)] * len(state.args_buffer))


# ---------------------------------------------------------------- tool entries


class TestToolEntry:
    def test_reads_the_fields_it_needs(self) -> None:
        entry = ToolEntry.from_payload({
            "name": "echo",
            "title": "回显",
            "description": "原样返回",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        })

        assert entry.name == "echo"
        assert entry.title == "回显"
        assert entry.required == ("text",)

    def test_a_missing_schema_is_tolerated(self) -> None:
        """``inputSchema`` is required by the spec, but in reality some servers omit
        it. One missing field should not keep the whole UI from starting."""
        entry = ToolEntry.from_payload({"name": "bare"})

        assert entry.name == "bare"
        assert entry.required == ()
        assert entry.schema == {}

    def test_a_wrongly_typed_required_is_ignored(self) -> None:
        """A non-array ``required`` must not be forced into use as an array — that is a
        crash, not a graceful degradation."""
        entry = ToolEntry.from_payload({"name": "x", "inputSchema": {"required": "text"}})

        assert entry.required == ()

    def test_matching_looks_at_more_than_the_name(self) -> None:
        """The user remembers "that tool that does arithmetic", not that it is named
        ``add``."""
        entry = _tool("add", description="计算两个数字之和")

        assert entry.matches("add")
        assert entry.matches("计算")
        assert not entry.matches("weather")

    def test_an_empty_needle_matches_everything(self) -> None:
        assert _tool("add").matches("")


# ---------------------------------------------------------------- filtering


class TestFiltering:
    def test_focus_moves_through_the_panes(self) -> None:
        state = _run(_state_with_tools("a"), _key(Key.TAB))
        assert state.pane is Pane.WIRE
        state = _run(state, _key(Key.TAB))
        assert state.pane is Pane.TIMELINE
        state = _run(state, _key(Key.TAB))
        assert state.pane is Pane.TOOLS

    def test_slash_enters_filter_mode(self) -> None:
        state = _run(_state_with_tools("alpha", "beta"), _press("/"))
        assert state.mode is Mode.FILTER

    def test_typing_narrows_the_list(self) -> None:
        state = _state_with_tools("alpha", "beta", "gamma")
        state = _run(state, _press("/"), _press("a"), _press("l"))

        assert state.filter_text == "al"
        assert [t.name for t in visible_tools(state)] == ["alpha"]

    def test_backspace_widens_it_again(self) -> None:
        state = _state_with_tools("alpha", "beta")
        state = _run(state, _press("/"), _press("b"))
        assert [t.name for t in visible_tools(state)] == ["beta"]

        state = _run(state, _key(Key.BACKSPACE))
        assert state.filter_text == ""
        assert [t.name for t in visible_tools(state)] == ["alpha", "beta"]

    def test_enter_keeps_the_filter(self) -> None:
        """Enter means "show me this filter", not "cancel the filter"."""
        state = _state_with_tools("alpha", "beta")
        state = _run(state, _press("/"), _press("b"), _key(Key.ENTER))

        assert state.mode is Mode.BROWSE
        assert state.filter_text == "b"
        assert [t.name for t in visible_tools(state)] == ["beta"]

    def test_escape_drops_the_filter(self) -> None:
        """ESC means "never mind", so the word must be cleared — leaving it while
        exiting filter mode is more confusing."""
        state = _state_with_tools("alpha", "beta")
        state = _run(state, _press("/"), _press("b"), _key(Key.ESC))

        assert state.mode is Mode.BROWSE
        assert state.filter_text == ""
        assert len(visible_tools(state)) == 2

    def test_q_is_a_filter_character_not_quit(self) -> None:
        """While typing the filter "query", the first letter must not quit the program."""
        state = _state_with_tools("alpha")
        step = apply_key(_run(state, _press("/")), _press("q"))
        step = apply_key(step.state, _press("u"))

        assert step.action is TuiAction.NONE
        assert step.state.filter_text == "qu"

    def test_the_selection_resets_when_the_filter_changes(self) -> None:
        """The old index does not point at the same tool in the new list."""
        state = _state_with_tools("alpha", "beta", "gamma")
        state = _run(state, _key(Key.DOWN), _key(Key.DOWN))
        assert state.selected_tool == 2

        state = _run(state, _press("/"), _press("g"))
        assert state.selected_tool == 0


# ---------------------------------------------------------------- moving the selection


class TestToolSelection:
    def test_down_moves_down_the_list(self) -> None:
        state = _run(_state_with_tools("a", "b", "c"), _key(Key.DOWN))
        assert state.selected_tool == 1

    def test_up_moves_up_the_list(self) -> None:
        """In the tool list ``↑`` decrements the index, unlike the wire pane where ``↑``
        scrolls back through history."""
        state = _state_with_tools("a", "b", "c")
        state = _run(state, _key(Key.DOWN), _key(Key.UP))
        assert state.selected_tool == 0

    def test_the_list_wraps_around(self) -> None:
        """With only a few entries, stopping at the end makes people think it froze."""
        state = _run(_state_with_tools("a", "b"), _key(Key.DOWN), _key(Key.DOWN))
        assert state.selected_tool == 0

    def test_wrapping_backwards_too(self) -> None:
        state = _run(_state_with_tools("a", "b"), _key(Key.UP))
        assert state.selected_tool == 1

    def test_home_and_end_jump(self) -> None:
        state = _state_with_tools("a", "b", "c", "d")
        assert _run(state, _key(Key.END)).selected_tool == 3
        assert _run(state, _key(Key.HOME)).selected_tool == 0

    def test_moving_in_an_empty_list_is_harmless(self) -> None:
        """Key presses must not blow up when there are no tools — which is the case
        when the server declares no tools capability."""
        state = _run(initial_state(), _key(Key.DOWN), _key(Key.PAGE_UP))
        assert state.selected_tool == 0


# ---------------------------------------------------------------- the wire pane


class TestWireScroll:
    def _with_frames(self, count: int) -> TuiState:
        """Build ``count`` frames with the focus on the wire pane.

        ``↑↓`` only acts on the focused pane; all three panes share the arrow keys and
        the focus decides where they go.
        """
        state = _run(initial_state(), _key(Key.TAB))
        for index in range(count):
            state = push_frame(state, request_frame("tools/list", f"line{index}"))
        return state

    def test_new_frames_stay_pinned_to_the_bottom(self) -> None:
        state = self._with_frames(3)
        assert state.wire_offset == 0

    def test_up_scrolls_back_through_history(self) -> None:
        state = _run(self._with_frames(5), _key(Key.UP))
        assert state.wire_offset == 1

    def test_down_comes_back_towards_the_newest(self) -> None:
        state = _run(self._with_frames(5), _key(Key.UP), _key(Key.UP), _key(Key.DOWN))
        assert state.wire_offset == 1

    def test_scrolling_back_is_clamped_to_the_oldest(self) -> None:
        state = self._with_frames(3)
        state = _run(state, *[_key(Key.UP)] * 10)
        assert state.wire_offset == 2  # 3 frames → max offset 2

    def test_end_jumps_back_to_the_newest(self) -> None:
        state = _run(self._with_frames(5), _key(Key.UP), _key(Key.END))
        assert state.wire_offset == 0

    def test_home_jumps_to_the_oldest(self) -> None:
        state = _run(self._with_frames(4), _key(Key.HOME))
        assert state.wire_offset == 3

    def test_a_new_frame_does_not_yank_a_scrolled_view(self) -> None:
        """While reading history, a newly arrived frame must not yank the view.

        The server is often still emitting while the user stares at earlier frames;
        bumping the offset by one keeps the frame they were on in place.
        """
        state = _run(self._with_frames(5), _key(Key.UP), _key(Key.UP))
        before = state.frames[len(state.frames) - 1 - state.wire_offset]

        state = push_frame(state, request_frame("tools/list", "new"))

        after = state.frames[len(state.frames) - 1 - state.wire_offset]
        assert after.seq == before.seq

    def test_the_frame_count_is_capped(self) -> None:
        """Piling up a long session (tens of thousands of frames) is a memory leak."""
        state = initial_state()
        for index in range(MAX_FRAMES + 20):
            state = push_frame(state, request_frame("m", str(index)))

        assert len(state.frames) == MAX_FRAMES

    def test_sequence_numbers_survive_trimming(self) -> None:
        """Sequence numbers must not be derived from the index — after frames are
        dropped, indexes shift wholesale."""
        state = initial_state()
        for index in range(MAX_FRAMES + 5):
            state = push_frame(state, request_frame("m", str(index)))

        seqs = [entry.seq for entry in state.frames]
        assert seqs == list(range(6, MAX_FRAMES + 6))

    def test_entering_toggles_expansion(self) -> None:
        state = _run(self._with_frames(3), _key(Key.ENTER))

        assert state.expanded_seq == state.frames[-1].seq

    def test_entering_again_collapses_it(self) -> None:
        state = _run(self._with_frames(3), _key(Key.ENTER), _key(Key.ENTER))

        assert state.expanded_seq is None

    def test_escape_collapses_whatever_is_expanded(self) -> None:
        """ESC uniformly means "step back one level"."""
        state = _run(self._with_frames(3), _key(Key.ENTER), _key(Key.ESC))

        assert state.expanded_seq is None

    def test_enter_on_the_tools_pane_does_not_expand(self) -> None:
        """The same Enter key means "call" in the tool pane and only "expand" in the
        wire pane."""
        state = initial_state()
        state = push_frame(state, request_frame("m", "x"))
        step = apply_key(state, _key(Key.ENTER))

        assert step.state.expanded_seq is None

    def test_expanding_tracks_the_frame_not_the_index(self) -> None:
        """After frames are dropped, the expanded one should still be that frame, not
        whatever now sits at the same index."""
        state = _run(self._with_frames(3), _key(Key.ENTER))
        target = state.expanded_seq

        for index in range(MAX_FRAMES + 5):
            state = push_frame(state, request_frame("m", str(index)))

        assert state.expanded_seq == target
        assert all(entry.seq != target for entry in state.frames)


# ---------------------------------------------------------------- timeline


class TestTimeline:
    def _with_calls(self, count: int) -> TuiState:
        """Build ``count`` call records and put the focus on the timeline pane."""
        state = _run(initial_state(), _key(Key.TAB), _key(Key.TAB))
        for index in range(count):
            state = push_call(state, CallRecord("tools/call", float(index), ok=True))
        return state

    def test_calls_are_recorded(self) -> None:
        state = self._with_calls(3)
        assert len(state.calls) == 3
        assert state.timeline_offset == 0

    def test_the_offset_is_clamped(self) -> None:
        state = self._with_calls(2)
        state = _run(state, *[_key(Key.UP)] * 5)
        assert state.timeline_offset == 1

    def test_a_new_call_does_not_yank_a_scrolled_timeline(self) -> None:
        state = _run(self._with_calls(4), _key(Key.UP))
        before = state.calls[len(state.calls) - 1 - state.timeline_offset]

        state = push_call(state, CallRecord("tools/call", 9.0, ok=True))

        after = state.calls[len(state.calls) - 1 - state.timeline_offset]
        assert after is before

    def test_the_call_count_is_capped(self) -> None:
        state = initial_state()
        for index in range(600):
            state = push_call(state, CallRecord("m", float(index), ok=True))
        assert len(state.calls) == 500


# ---------------------------------------------------------------- args skeleton


class TestArgsSkeleton:
    def test_only_required_parameters_are_included(self) -> None:
        """Stuffing optional parameters in too means an immediate Enter would send a
        pile of placeholder values as real arguments — the server would not error, it
        would just return a result based on wrong input."""
        tool = ToolEntry.from_payload({
            "name": "echo",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "loud": {"type": "boolean"}},
                "required": ["text"],
            },
        })

        assert json.loads(args_skeleton(tool)) == {"text": ""}

    def test_placeholders_follow_the_declared_type(self) -> None:
        tool = ToolEntry.from_payload({
            "name": "x",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "s": {"type": "string"},
                    "n": {"type": "number"},
                    "i": {"type": "integer"},
                    "b": {"type": "boolean"},
                    "a": {"type": "array"},
                    "o": {"type": "object"},
                },
                "required": ["s", "n", "i", "b", "a", "o"],
            },
        })

        assert json.loads(args_skeleton(tool)) == {
            "s": "",
            "n": 0,
            "i": 0,
            "b": False,
            "a": [],
            "o": {},
        }

    def test_a_tool_without_required_parameters_gives_an_empty_object(self) -> None:
        assert args_skeleton(_tool("ping")) == "{}"

    def test_an_unknown_type_falls_back_to_null(self) -> None:
        """A missing ``type`` in the schema is common. ``null`` is more honest than
        ``""`` — the latter suggests a string should be filled in."""
        tool = ToolEntry.from_payload({
            "name": "x",
            "inputSchema": {"properties": {"any": {}}, "required": ["any"]},
        })

        assert json.loads(args_skeleton(tool)) == {"any": None}


class TestValueStart:
    """The helper for where the cursor lands. It is what makes the editing feel like
    fewer keystrokes."""

    def test_the_cursor_lands_inside_string_quotes(self) -> None:
        """It lands inside the quotes, so typing directly fills the value in."""
        text = '{"text": ""}'
        pos = _first_value_start(text, 0)

        assert text[pos - 1] == '"'
        assert text[pos] == '"'

    def test_the_cursor_lands_before_a_non_string_value(self) -> None:
        text = '{"n": 0}'
        assert text[_first_value_start(text, 0)] == "0"

    def test_it_finds_the_next_value_from_a_cursor(self) -> None:
        text = '{"a": "", "b": ""}'
        first = _first_value_start(text, 0)
        second = _first_value_start(text, first + 1)

        assert first < second
        assert text[second - 1] == '"'

    def test_no_colon_left_gives_the_end(self) -> None:
        assert _first_value_start("no colon here", 0) == len("no colon here")


# ---------------------------------------------------------------- calling a tool


class TestCalling:
    def test_a_tool_without_required_parameters_is_called_directly(self) -> None:
        """With no required parameters, the user should not be sent through an args line."""
        step = apply_key(_state_with_tools("ping"), _key(Key.ENTER))

        assert step.action is TuiAction.CALL_TOOL
        assert step.call is not None
        assert step.call.tool == "ping"
        assert step.call.arguments == {}

    def test_a_tool_with_required_parameters_opens_the_args_line(self) -> None:
        state = set_tools(initial_state(), (_tool("echo", required=("text",)),))
        step = apply_key(state, _key(Key.ENTER))

        assert step.action is TuiAction.NONE
        assert step.state.mode is Mode.ARGS
        assert step.state.args_tool == "echo"
        assert json.loads(step.state.args_buffer) == {"text": ""}

    def test_the_cursor_starts_inside_the_first_placeholder(self) -> None:
        """Otherwise the user must press the right arrow once before typing."""
        state = set_tools(initial_state(), (_tool("echo", required=("text",)),))
        step = apply_key(state, _key(Key.ENTER))
        buf, pos = step.state.args_buffer, step.state.args_cursor

        assert buf[pos - 1] == '"'
        assert buf[pos] == '"'

    def test_typing_replaces_the_placeholder(self) -> None:
        state = set_tools(initial_state(), (_tool("echo", required=("text",)),))
        state = apply_key(state, _key(Key.ENTER)).state
        state = _run(state, _press("h"), _press("i"))

        assert json.loads(state.args_buffer) == {"text": "hi"}

    def test_tab_jumps_to_the_next_parameter(self) -> None:
        state = set_tools(
            initial_state(), (_tool("pair", required=("a", "b")),)
        )
        state = apply_key(state, _key(Key.ENTER)).state
        first = state.args_cursor

        state = _run(state, _key(Key.TAB))
        assert state.args_cursor > first

    def test_submitting_gives_a_validated_call(self) -> None:
        state = set_tools(initial_state(), (_tool("echo", required=("text",)),))
        state = apply_key(state, _key(Key.ENTER)).state
        state = _run(state, _press("h"), _press("i"))
        step = apply_key(state, _key(Key.ENTER))

        assert step.action is TuiAction.CALL_TOOL
        assert step.call == PendingCall("echo", {"text": "hi"})
        assert step.state.mode is Mode.BROWSE
        assert step.state.busy is True

    def test_escape_cancels_and_clears_the_buffer(self) -> None:
        """Leaving half-finished edits behind would give a shock on the next entry."""
        state = set_tools(initial_state(), (_tool("echo", required=("text",)),))
        state = apply_key(state, _key(Key.ENTER)).state
        state = _run(state, _press("x"), _key(Key.ESC))

        assert state.mode is Mode.BROWSE
        assert state.args_buffer == ""
        assert state.args_tool == ""

    def test_broken_json_is_reported_not_sent(self) -> None:
        """It must not be sent: the server would only say "invalid arguments", while the
        problem can be pointed out locally at once."""
        state = set_tools(initial_state(), (_tool("echo", required=("text",)),))
        state = apply_key(state, _key(Key.ENTER)).state
        state = _run(state, _key(Key.BACKSPACE), _key(Key.BACKSPACE), _key(Key.BACKSPACE))
        step = apply_key(state, _key(Key.ENTER))

        assert step.action is TuiAction.NONE
        assert step.state.args_problem is ArgsProblem.NOT_JSON

    def test_a_json_array_is_not_an_argument_object(self) -> None:
        """Arguments must be an object. An array is valid JSON too, so this needs its
        own check."""
        state = set_tools(initial_state(), (_tool("echo", required=("text",)),))
        state = apply_key(state, _key(Key.ENTER)).state
        state = _clear_args(state)
        state = _run(state, _press("["), _press("]"))
        step = apply_key(state, _key(Key.ENTER))

        assert step.state.args_problem is ArgsProblem.NOT_OBJECT

    def test_a_missing_required_parameter_is_named(self) -> None:
        """A missing parameter must be named — "bad arguments" gives nothing to act on."""
        state = set_tools(initial_state(), (_tool("echo", required=("text",)),))
        state = apply_key(state, _key(Key.ENTER)).state
        state = _clear_args(state)
        state = _run(state, _press("{"), _press("}"))
        step = apply_key(state, _key(Key.ENTER))

        assert step.state.args_problem is ArgsProblem.MISSING_REQUIRED
        assert step.state.missing_param == "text"

    def test_editing_clears_a_previous_problem(self) -> None:
        """After an error, the first key press must clear the message — otherwise the
        user fixes it and still stares at the old error."""
        state = set_tools(initial_state(), (_tool("echo", required=("text",)),))
        state = apply_key(state, _key(Key.ENTER)).state
        state = _run(state, _key(Key.BACKSPACE), _key(Key.ENTER))
        assert state.args_problem is not None

        state = _run(state, _press("x"))
        assert state.args_problem is None

    def test_calling_nothing_is_a_no_op(self) -> None:
        step = apply_key(initial_state(), _key(Key.ENTER))
        assert step.action is TuiAction.NONE


# ---------------------------------------------------------------- cursor editing


class TestArgsEditing:
    def _in_args(self, buffer: str) -> TuiState:
        """Build a state already in ARGS mode — saves walking through the mode entry
        every time."""
        state = set_tools(initial_state(), (_tool("echo", required=("text",)),))
        state = apply_key(state, _key(Key.ENTER)).state
        return replace(state, args_buffer=buffer, args_cursor=len(buffer))

    def test_backspace_deletes_before_the_cursor(self) -> None:
        state = _run(self._in_args("ab"), _key(Key.BACKSPACE))
        assert state.args_buffer == "a"

    def test_backspace_at_the_start_does_nothing(self) -> None:
        state = _run(self._in_args("ab"), _key(Key.HOME), _key(Key.BACKSPACE))
        assert state.args_buffer == "ab"

    def test_delete_removes_at_the_cursor(self) -> None:
        state = _run(self._in_args("ab"), _key(Key.HOME), _key(Key.DELETE))
        assert state.args_buffer == "b"

    def test_delete_at_the_end_does_nothing(self) -> None:
        state = _run(self._in_args("ab"), _key(Key.DELETE))
        assert state.args_buffer == "ab"

    def test_the_cursor_moves_left_and_right(self) -> None:
        state = _run(self._in_args("ab"), _key(Key.LEFT))
        assert state.args_cursor == 1
        state = _run(state, _key(Key.RIGHT))
        assert state.args_cursor == 2

    def test_the_cursor_stops_at_both_ends(self) -> None:
        state = _run(self._in_args("ab"), *[_key(Key.LEFT)] * 5)
        assert state.args_cursor == 0
        state = _run(state, *[_key(Key.RIGHT)] * 5)
        assert state.args_cursor == 2

    def test_insertion_happens_at_the_cursor(self) -> None:
        state = _run(self._in_args("ac"), _key(Key.LEFT), _press("b"))
        assert state.args_buffer == "abc"


# ---------------------------------------------------------------- misc


class TestServerAndSettle:
    def test_setting_the_server_keeps_the_transport(self) -> None:
        from mcpdump.core.session import ServerInfo

        state = set_server(initial_state(), ServerInfo(name="echo"), "stdio · x")

        assert state.server is not None
        assert state.server.name == "echo"
        assert state.transport == "stdio · x"

    def test_settle_clears_busy(self) -> None:
        """``busy`` is cleared in this one place only. Leaving callers to ``replace``
        it themselves means someone eventually forgets, and the UI is stuck on
        "running" forever."""
        state = settle(TuiState(busy=True), "boom")

        assert state.busy is False
        assert state.error_text == "boom"

    def test_an_unknown_key_changes_nothing(self) -> None:
        """An unrecognised key must do nothing; guessing some arrow key would make the
        cursor jump for no reason."""
        state = _state_with_tools("a", "b")
        step = apply_key(state, _key(Key.UNKNOWN))

        assert step.state == state
        assert step.action is TuiAction.NONE

    def test_q_quits(self) -> None:
        step = apply_key(_state_with_tools("a"), _press("q"))
        assert step.action is TuiAction.QUIT

    def test_an_unbound_character_does_nothing_in_browse_mode(self) -> None:
        """Typing in browse mode must have no effect — no input box is waiting for it."""
        state = _state_with_tools("a")
        assert apply_key(state, _press("z")).state == state


def test_the_state_module_has_no_user_visible_text() -> None:
    """The state machine holds no user-visible sentence.

    All wording is rendered by the view layer through ``i18n.t()``. AST rather than
    string search, because a docstring that mentions ``i18n.t()`` is explaining the
    design, not using it.
    """
    import ast

    import mcpdump.ui.tui_state as module

    tree = ast.parse(pathlib.Path(module.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "i18n", "the state machine must not import the message table"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "t", "an i18n.t() call must not appear in the state machine"

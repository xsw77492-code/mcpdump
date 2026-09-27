"""TUI state machine: a key goes in, a new state comes out. Pure functions.

``apply_key(state, press) -> TuiStep(state, action, call)``, so "what key
produced what screen" can be verified offline. Calling a tool does not happen
here: ``apply_key`` only produces a ``TuiAction.CALL_TOOL`` and a
``PendingCall``, and ``commands/tui.py`` does the connecting.

The state carries data and enums only; ``tui_view.py`` renders it through
``i18n.t()``, which keeps the state machine language-independent.

Scroll positions measure distance from the newest entry, so 0 is pinned to the
latest: new data arrives at the tail, and indices shift where distances do not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from ..core.session import ServerInfo
from .keys import Key, KeyPress
from .wire import Frame

__all__ = [
    "MAX_CALLS",
    "MAX_FRAMES",
    "PAGE_STEP",
    "ArgsProblem",
    "CallRecord",
    "Mode",
    "Pane",
    "PendingCall",
    "ToolEntry",
    "TuiAction",
    "TuiState",
    "TuiStep",
    "WireEntry",
    "apply_key",
    "args_skeleton",
    "initial_state",
    "push_call",
    "push_frame",
    "set_server",
    "set_tools",
    "settle",
    "visible_tools",
]

#: Cap on retained frames. A long session would otherwise grow without bound,
#: the same reason ``core/proxy.py`` caps its pending table. The oldest go first.
MAX_FRAMES = 2000

#: Cap on retained calls in the timeline.
MAX_CALLS = 500

#: Rows a page-up or page-down key covers.
PAGE_STEP = 10


class Pane(str, Enum):
    """Three of the four panes can take focus; the server pane is read-only."""

    TOOLS = "tools"
    WIRE = "wire"
    TIMELINE = "timeline"


class Mode(str, Enum):
    """Input mode, and the only thing that routes key presses.

    ``q`` does not quit in ``FILTER`` mode: typing "query" into the filter would
    otherwise close the program on its first letter.
    """

    BROWSE = "browse"
    FILTER = "filter"
    ARGS = "args"


class TuiAction(str, Enum):
    """Work the key press asks of the outside world; the state machine is pure."""

    NONE = "none"
    QUIT = "quit"
    CALL_TOOL = "call_tool"


class ArgsProblem(str, Enum):
    """What is wrong with the argument line. An enum, not a sentence.

    Three cases rather than one "bad arguments" because the fixes differ.
    """

    NOT_JSON = "not_json"
    NOT_OBJECT = "not_object"
    MISSING_REQUIRED = "missing_required"


@dataclass(frozen=True)
class ToolEntry:
    """A tool as the interface sees it. The raw schema is kept for the skeleton."""

    name: str
    title: str
    description: str
    required: tuple[str, ...]
    schema: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ToolEntry:
        schema = payload.get("inputSchema")
        if not isinstance(schema, dict):
            schema = {}
        required = schema.get("required")
        return cls(
            name=str(payload.get("name", "")),
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            required=tuple(str(r) for r in required) if isinstance(required, list) else (),
            schema=schema,
        )

    def matches(self, needle: str) -> bool:
        """Match the name, title or description.

        Users remember "the one that does arithmetic", not that it is ``add``.
        """
        if not needle:
            return True
        low = needle.lower()
        return any(
            low in field.lower() for field in (self.name, self.title, self.description)
        )


@dataclass(frozen=True)
class CallRecord:
    """One timeline row: the duration and outcome of a call."""

    method: str
    elapsed_ms: float
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class WireEntry:
    """A frame with a sequence number.

    The sequence number is required, not a list index: once the frame cap starts
    dropping the oldest, indices shift, and "the frame that was expanded"
    silently becomes a different frame.
    """

    seq: int
    frame: Frame


@dataclass(frozen=True)
class PendingCall:
    """A call about to be made. Arguments are validated: a server should never
    receive a request with a required parameter missing.
    """

    tool: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class TuiState:
    """Everything the interface knows at one moment.

    ``frozen=True`` is deliberate: the state is replaced wholesale by
    ``apply_key`` and never mutated, so "before and after this key" is testable.
    """

    server: ServerInfo | None = None
    transport: str = ""
    tools: tuple[ToolEntry, ...] = ()
    frames: tuple[WireEntry, ...] = ()
    calls: tuple[CallRecord, ...] = ()

    pane: Pane = Pane.TOOLS
    mode: Mode = Mode.BROWSE

    filter_text: str = ""
    selected_tool: int = 0
    wire_offset: int = 0
    timeline_offset: int = 0
    expanded_seq: int | None = None

    args_tool: str = ""
    args_buffer: str = ""
    args_cursor: int = 0
    args_problem: ArgsProblem | None = None
    missing_param: str = ""

    #: Set while a call is in flight. Calls are synchronous, so this shows up as
    #: a frozen wait on screen.
    busy: bool = False
    #: Raw error text handed in from outside (an exception message), not a label.
    error_text: str = ""

    next_seq: int = 1


@dataclass(frozen=True)
class TuiStep:
    """The result of one key press; the only thing the state machine hands out."""

    state: TuiState
    action: TuiAction = TuiAction.NONE
    call: PendingCall | None = None


# ------------------------------------------------------------ construction


def initial_state(
    *,
    server: ServerInfo | None = None,
    transport: str = "",
    tools: tuple[ToolEntry, ...] = (),
) -> TuiState:
    return TuiState(server=server, transport=transport, tools=tools)


def set_server(state: TuiState, server: ServerInfo, transport: str) -> TuiState:
    return replace(state, server=server, transport=transport)


def set_tools(state: TuiState, tools: tuple[ToolEntry, ...]) -> TuiState:
    """Swap the tool list. The selection resets: the old index means nothing here."""
    return replace(state, tools=tools, selected_tool=0)


def settle(state: TuiState, error_text: str = "") -> TuiState:
    """A call finished: clear the busy flag and attach a failure reason.

    This is the only place ``busy`` is cleared; otherwise someone eventually
    forgets and the interface waits forever on a result that never comes.
    """
    return replace(state, busy=False, error_text=error_text)


def visible_tools(state: TuiState) -> tuple[ToolEntry, ...]:
    """The tools matching the current filter; all of them when it is empty."""
    return tuple(tool for tool in state.tools if tool.matches(state.filter_text))


def push_frame(state: TuiState, frame: Frame) -> TuiState:
    """Append one frame.

    When ``wire_offset`` is 0 (pinned to the newest) it stays 0 and the view
    follows along; when the user is reading history the offset increments so the
    frame under their eyes stays put.
    """
    entry = WireEntry(seq=state.next_seq, frame=frame)
    frames = (*state.frames, entry)
    if len(frames) > MAX_FRAMES:
        frames = frames[-MAX_FRAMES:]

    offset = state.wire_offset + 1 if state.wire_offset > 0 else 0
    return replace(
        state,
        frames=frames,
        wire_offset=_clamp_offset(offset, len(frames)),
        next_seq=state.next_seq + 1,
    )


def push_call(state: TuiState, record: CallRecord) -> TuiState:
    """Append a call record. Scrolling behaves as in the message view, for the
    same reason.
    """
    calls = (*state.calls, record)
    if len(calls) > MAX_CALLS:
        calls = calls[-MAX_CALLS:]

    offset = state.timeline_offset + 1 if state.timeline_offset > 0 else 0
    return replace(
        state,
        calls=calls,
        timeline_offset=_clamp_offset(offset, len(calls)),
    )


def _clamp_offset(offset: int, total: int) -> int:
    """Clamp an offset to ``[0, total - 1]``, always 0 for an empty list."""
    if total <= 0:
        return 0
    return max(0, min(offset, total - 1))


# ------------------------------------------------------------ argument skeleton


def _placeholder(spec: Any) -> Any:
    """A placeholder value for a JSON Schema ``type``.

    Display only, not an invitation to send as-is: the skeleton has to spell out
    what would be sent.
    """
    if not isinstance(spec, dict):
        return None
    kind = spec.get("type")
    if kind == "string":
        return ""
    if kind in ("number", "integer"):
        return 0
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    if kind == "object":
        return {}
    return None


def args_skeleton(tool: ToolEntry) -> str:
    """Build an editable line of JSON from the schema.

    Required parameters only. Including optional ones means pressing Enter
    straight away sends a pile of placeholders (``""``, ``0``) as real
    arguments, and the server answers on wrong input.
    """
    props = tool.schema.get("properties")
    if not isinstance(props, dict):
        props = {}
    payload = {name: _placeholder(props.get(name)) for name in tool.required}
    return json.dumps(payload, ensure_ascii=False)


def _first_value_start(text: str, cursor: int) -> int:
    """Find the first value after ``cursor``, landing inside quotes for strings.

    Landing inside the quotes means typing fills the value straight away.
    """
    colon = text.find(":", cursor)
    if colon < 0:
        return len(text)
    pos = colon + 1
    while pos < len(text) and text[pos] == " ":
        pos += 1
    if pos < len(text) and text[pos] == '"':
        return pos + 1
    return pos


# ------------------------------------------------------------ key dispatch


def apply_key(state: TuiState, press: KeyPress) -> TuiStep:
    """One key press to a new state, plus any work for the outside world.

    An unrecognised key returns the state unchanged: guessing a direction key
    would make the cursor jump for no reason.
    """
    if state.mode is Mode.FILTER:
        return _apply_filter_key(state, press)
    if state.mode is Mode.ARGS:
        return _apply_args_key(state, press)
    return _apply_browse_key(state, press)


# ---- BROWSE ----


def _apply_browse_key(state: TuiState, press: KeyPress) -> TuiStep:
    key = press.key

    if key is Key.CHAR and press.char == "q":
        return TuiStep(state, TuiAction.QUIT)
    if key is Key.CHAR and press.char == "/":
        return TuiStep(replace(state, mode=Mode.FILTER))
    if key is Key.TAB:
        return TuiStep(replace(state, pane=_next_pane(state.pane)))
    if key is Key.ESC:
        # Collapse the expanded frame. ESC always undoes the most recent dive.
        return TuiStep(replace(state, expanded_seq=None))

    if key is Key.ENTER:
        return _activate(state)

    if key in (Key.UP, Key.DOWN, Key.HOME, Key.END, Key.PAGE_UP, Key.PAGE_DOWN):
        return TuiStep(_move(state, key))
    return TuiStep(state)


def _activate(state: TuiState) -> TuiStep:
    """Enter: what it does depends on the focused pane."""
    if state.pane is Pane.WIRE:
        return TuiStep(_toggle_expanded(state))
    if state.pane is not Pane.TOOLS:
        return TuiStep(state)

    tools = visible_tools(state)
    if not tools:
        return TuiStep(state)
    tool = tools[min(state.selected_tool, len(tools) - 1)]

    if not tool.required:
        # Nothing required, so call it directly; the argument line is a detour.
        return TuiStep(
            replace(state, busy=True, error_text=""),
            TuiAction.CALL_TOOL,
            PendingCall(tool.name, {}),
        )

    skeleton = args_skeleton(tool)
    return TuiStep(
        replace(
            state,
            mode=Mode.ARGS,
            args_tool=tool.name,
            args_buffer=skeleton,
            args_cursor=_first_value_start(skeleton, 0),
            args_problem=None,
            missing_param="",
            error_text="",
        )
    )


def _toggle_expanded(state: TuiState) -> TuiState:
    """Expand or collapse the selected frame; by sequence number, see ``WireEntry``."""
    entry = _selected_wire(state)
    if entry is None:
        return state
    if state.expanded_seq == entry.seq:
        return replace(state, expanded_seq=None)
    return replace(state, expanded_seq=entry.seq)


def _move(state: TuiState, key: Key) -> TuiState:
    """Dispatch movement to the focused pane: same keys, three different meanings."""
    if state.pane is Pane.TOOLS:
        return _move_tools(state, key)
    if state.pane is Pane.WIRE:
        return _move_wire(state, key)
    return _move_timeline(state, key)


def _move_tools(state: TuiState, key: Key) -> TuiState:
    """The tool list wraps around: with only a handful of entries, stopping at
    the end looks like a freeze.

    ``up`` decreases the index here, opposite to the message view and timeline,
    where it scrolls back and so increases the offset.
    """
    total = len(visible_tools(state))
    if total <= 0:
        return state
    if key is Key.HOME:
        index = 0
    elif key is Key.END:
        index = total - 1
    else:
        index = (state.selected_tool - _step_of(key)) % total
    return replace(state, selected_tool=max(0, min(index, total - 1)))


def _move_wire(state: TuiState, key: Key) -> TuiState:
    total = len(state.frames)
    if total <= 0:
        return state
    if key is Key.HOME:
        offset = total - 1  # the oldest frame
    elif key is Key.END:
        offset = 0
    else:
        offset = state.wire_offset + _step_of(key)
    return replace(state, wire_offset=_clamp_offset(offset, total))


def _move_timeline(state: TuiState, key: Key) -> TuiState:
    total = len(state.calls)
    if total <= 0:
        return state
    if key is Key.HOME:
        offset = total - 1
    elif key is Key.END:
        offset = 0
    else:
        offset = state.timeline_offset + _step_of(key)
    return replace(state, timeline_offset=_clamp_offset(offset, total))


def _step_of(key: Key) -> int:
    """How far one key moves, with distance-from-newest as the positive
    direction; ``up`` scrolls back and is therefore +1.
    """
    if key is Key.UP:
        return 1
    if key is Key.DOWN:
        return -1
    if key is Key.PAGE_UP:
        return PAGE_STEP
    if key is Key.PAGE_DOWN:
        return -PAGE_STEP
    return 0


def _next_pane(pane: Pane) -> Pane:
    order = (Pane.TOOLS, Pane.WIRE, Pane.TIMELINE)
    return order[(order.index(pane) + 1) % len(order)]


def _selected_wire(state: TuiState) -> WireEntry | None:
    """The frame the current offset points at, counting back from the newest."""
    if not state.frames:
        return None
    index = len(state.frames) - 1 - state.wire_offset
    if not 0 <= index < len(state.frames):
        return None
    return state.frames[index]


# ---- FILTER ----


def _apply_filter_key(state: TuiState, press: KeyPress) -> TuiStep:
    if press.key is Key.ESC:
        # ESC cancels the filter outright: text cleared, selection reset.
        return TuiStep(
            replace(state, mode=Mode.BROWSE, filter_text="", selected_tool=0)
        )
    if press.key is Key.ENTER:
        return TuiStep(replace(state, mode=Mode.BROWSE))
    if press.key is Key.BACKSPACE:
        return TuiStep(_set_filter(state, state.filter_text[:-1]))
    if press.key is Key.CHAR and press.is_printable:
        return TuiStep(_set_filter(state, state.filter_text + press.char))
    return TuiStep(state)


def _set_filter(state: TuiState, text: str) -> TuiState:
    """A changed filter resets the selection: the old index points elsewhere now."""
    return replace(state, filter_text=text, selected_tool=0)


# ---- ARGS ----


def _apply_args_key(state: TuiState, press: KeyPress) -> TuiStep:
    key = press.key

    if key is Key.ESC:
        return TuiStep(_leave_args(state))
    if key is Key.ENTER:
        return _submit_args(state)
    if key is Key.TAB:
        return TuiStep(
            replace(
                state,
                args_cursor=_first_value_start(state.args_buffer, state.args_cursor + 1),
            )
        )
    if key is Key.BACKSPACE:
        return TuiStep(_edit_args(state, delete_before=True))
    if key is Key.DELETE:
        return TuiStep(_edit_args(state, delete_before=False))
    if key in (Key.LEFT, Key.RIGHT, Key.HOME, Key.END):
        return TuiStep(_move_cursor(state, key))
    if key is Key.CHAR and press.is_printable:
        return TuiStep(_insert_args(state, press.char))
    return TuiStep(state)


def _leave_args(state: TuiState) -> TuiState:
    """Leave argument mode and clear it; a half-finished edit would be startling
    on the next visit.
    """
    return replace(
        state,
        mode=Mode.BROWSE,
        args_tool="",
        args_buffer="",
        args_cursor=0,
        args_problem=None,
        missing_param="",
    )


def _move_cursor(state: TuiState, key: Key) -> TuiState:
    if key is Key.LEFT:
        cursor = state.args_cursor - 1
    elif key is Key.RIGHT:
        cursor = state.args_cursor + 1
    elif key is Key.HOME:
        cursor = 0
    else:
        cursor = len(state.args_buffer)
    return replace(state, args_cursor=max(0, min(cursor, len(state.args_buffer))))


def _insert_args(state: TuiState, char: str) -> TuiState:
    pos = max(0, min(state.args_cursor, len(state.args_buffer)))
    buf = state.args_buffer[:pos] + char + state.args_buffer[pos:]
    return replace(
        state, args_buffer=buf, args_cursor=pos + len(char), args_problem=None
    )


def _edit_args(state: TuiState, *, delete_before: bool) -> TuiState:
    buf, pos = state.args_buffer, state.args_cursor
    if delete_before:
        if pos <= 0:
            return state
        buf, pos = buf[: pos - 1] + buf[pos:], pos - 1
    else:
        if pos >= len(buf):
            return state
        buf = buf[:pos] + buf[pos + 1 :]
    return replace(state, args_buffer=buf, args_cursor=pos, args_problem=None)


def _submit_args(state: TuiState) -> TuiStep:
    """Validate the argument line and, if it passes, hand over the call.

    Validation is local rather than left to a -32602 from the server: catching
    it here points at the exact key at fault. A request missing a required
    parameter should not go out at all.
    """
    try:
        parsed = json.loads(state.args_buffer or "{}")
    except ValueError:
        return TuiStep(replace(state, args_problem=ArgsProblem.NOT_JSON))
    if not isinstance(parsed, dict):
        return TuiStep(replace(state, args_problem=ArgsProblem.NOT_OBJECT))

    tool = _tool_named(state, state.args_tool)
    if tool is not None:
        for name in tool.required:
            if name not in parsed:
                return TuiStep(
                    replace(
                        state,
                        args_problem=ArgsProblem.MISSING_REQUIRED,
                        missing_param=name,
                    )
                )

    return TuiStep(
        replace(_leave_args(state), busy=True, error_text=""),
        TuiAction.CALL_TOOL,
        PendingCall(state.args_tool, parsed),
    )


def _tool_named(state: TuiState, name: str) -> ToolEntry | None:
    for tool in state.tools:
        if tool.name == name:
            return tool
    return None

"""UI layer: one place for terminal visuals and rendering.

Import from ``..ui`` rather than its submodules, so "colours from theme, widths
from width, symbols from symbols" is an enforceable rule instead of a convention.

``theme`` semantic colours · ``symbols`` symbol language · ``width`` display
width · ``layout`` three-part skeletons · ``render`` tables, panels and unit
formatting · ``wire`` message-flow view · ``keys`` single-key reads (TUI) ·
``tui_state`` TUI state machine (pure) · ``tui_view`` TUI four-pane view (pure)

This layer must not depend on ``services``: that would make ``runtime → ui →
services → runtime`` a cycle. Only generic primitives belong here.
"""

from . import keys, layout, symbols, theme, tui_state, tui_view, width, wire
from .keys import (
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
from .layout import (
    HINT_MARK,
    INDENT,
    bullet_lines,
    end_gap,
    hint_lines,
    indent_lines,
    join_blocks,
    justify_ends,
    key_value_lines,
    section_lines,
)
from .render import (
    format_duration,
    render_capabilities,
    render_error,
    render_json,
    render_next_steps,
    render_prompts,
    render_resources,
    render_server_header,
    render_tool_result,
    render_tools,
    styled_line,
    styled_lines,
)
from .symbols import SYMBOLS, Symbols, use_ascii
from .theme import THEME, console, err_console, make_console
from .tui_state import (
    MAX_CALLS,
    MAX_FRAMES,
    PAGE_STEP,
    ArgsProblem,
    CallRecord,
    Mode,
    Pane,
    PendingCall,
    ToolEntry,
    TuiAction,
    TuiState,
    TuiStep,
    WireEntry,
    apply_key,
    args_skeleton,
    initial_state,
    push_call,
    push_frame,
    set_server,
    set_tools,
    settle,
    visible_tools,
)
from .tui_view import (
    MIN_HEIGHT,
    MIN_WIDTH,
    OUTLIER_MIN_DELTA_MS,
    OUTLIER_RATIO,
    Layout,
    build_view,
    is_outlier,
    layout_of,
    water_bar,
)
from .width import (
    center,
    char_width,
    display_width,
    ljust,
    rjust,
    strip_ansi,
    truncate,
)
from .wire import (
    Frame,
    build_frame,
    build_wire,
    frames_of,
    render_frame,
    render_wire,
    request_frame,
    response_frame,
    summarize,
)

__all__ = [
    # submodules
    "keys",
    "layout",
    "symbols",
    "theme",
    "tui_state",
    "tui_view",
    "width",
    "wire",
    # keys
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
    # theme
    "THEME",
    "console",
    "err_console",
    "make_console",
    # symbols
    "SYMBOLS",
    "Symbols",
    "use_ascii",
    # width
    "center",
    "char_width",
    "display_width",
    "ljust",
    "rjust",
    "strip_ansi",
    "truncate",
    # layout
    "HINT_MARK",
    "INDENT",
    "bullet_lines",
    "end_gap",
    "hint_lines",
    "indent_lines",
    "join_blocks",
    "justify_ends",
    "key_value_lines",
    "section_lines",
    # wire
    "Frame",
    "build_frame",
    "build_wire",
    "frames_of",
    "render_frame",
    "render_wire",
    "request_frame",
    "response_frame",
    "summarize",
    # TUI state machine
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
    # TUI four-pane view
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "OUTLIER_MIN_DELTA_MS",
    "OUTLIER_RATIO",
    "Layout",
    "build_view",
    "is_outlier",
    "layout_of",
    "water_bar",
    # rendering
    "format_duration",
    "render_capabilities",
    "render_error",
    "render_json",
    "render_next_steps",
    "render_prompts",
    "render_resources",
    "render_server_header",
    "render_tool_result",
    "render_tools",
    "styled_line",
    "styled_lines",
]

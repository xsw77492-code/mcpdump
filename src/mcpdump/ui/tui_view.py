"""TUI four-pane view: state in, one screen of lines out. Pure functions.

``build_view(state, *, width, height)`` returns exactly ``height`` lines, each
exactly ``width`` display columns wide (``display_width``, not ``len``): a line
measured with ``len`` that holds wide characters comes out short and the borders
misalign. ``layout_of`` owns the row and column budget.

All output is built with ``Text``, never by interpolating Rich markup into an
f-string: ``[`` and ``]`` are ordinary in JSON.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rich import box as rich_box
from rich.text import Text

from ..i18n import t
from .render import format_duration
from .symbols import SYMBOLS, use_ascii
from .tui_state import (
    ArgsProblem,
    CallRecord,
    Mode,
    Pane,
    ToolEntry,
    TuiState,
    WireEntry,
    visible_tools,
)
from .width import char_width, display_width, ljust, rjust, truncate
from .wire import PAYLOAD_INDENT

__all__ = [
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "OUTLIER_MIN_DELTA_MS",
    "OUTLIER_RATIO",
    "Layout",
    "build_view",
    "is_outlier",
    "layout_of",
    "water_bar",
]

#: Below this size no frame is drawn: misaligned borders read worse than fewer panes.
MIN_WIDTH = 24
MIN_HEIGHT = 6

#: Two thresholds for "this call was slow" in the timeline. Both must hold: a
#: ratio alone flags microsecond jitter, an absolute delta alone makes an extra
#: 50 ms on a one-second call look significant.
OUTLIER_RATIO = 1.5
OUTLIER_MIN_DELTA_MS = 50.0

#: Rows taken by the top border, title, junction, bottom border and status bar.
_CHROME_ROWS = 5

_TIMELINE_MIN = 3
_TIMELINE_MAX = 6

#: Bar characters in eighths: index 0 is 1/8, index 6 is 7/8; a full cell uses ``█``.
_BAR_PARTIALS = "▏▎▍▌▋▊▉"
_BAR_FULL = "█"
_BAR_FULL_ASCII = "#"

_CURSOR = "▌"
_CURSOR_ASCII = "|"

_DIM = "mcpdump.dim"
_LABEL = "mcpdump.label"
_BRAND = "mcpdump.brand"
_META = "mcpdump.meta"
_WARN = "mcpdump.warn"
_ERR = "mcpdump.err"
_TYPE = "mcpdump.type"

_MODE_KEYS = {
    Mode.BROWSE: "tui.mode.browse",
    Mode.FILTER: "tui.mode.filter",
    Mode.ARGS: "tui.mode.args",
}


@dataclass(frozen=True)
class Layout:
    """Row and column budget for the four panes. When ``degraded`` is set the
    remaining fields mean nothing.
    """

    width: int
    height: int
    left_w: int
    right_w: int
    body_rows: int
    timeline_block: int
    degraded: bool

    @property
    def has_timeline(self) -> bool:
        return self.timeline_block > 0

    @property
    def call_rows(self) -> int:
        """How many calls fit in the timeline: the block minus its junction and summary."""
        return max(0, self.timeline_block - 2)


def layout_of(width: int, height: int, *, has_calls: bool) -> Layout:
    """Compute the budget for a terminal size. The only implementation: split it
    up and the panes stop lining up.

    ``left_w`` is clamped proportionally first, then pushed back by the right
    pane's minimum: the message view is the point of the tool and must not be
    squeezed out to please the list.
    """
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return Layout(
            width=width,
            height=height,
            left_w=0,
            right_w=0,
            body_rows=0,
            timeline_block=0,
            degraded=True,
        )

    inner = width - 3
    left = min(40, max(22, width * 30 // 100))
    left = max(6, min(left, inner * 55 // 100))
    left = min(left, inner - 6)
    right = inner - left

    room = height - _CHROME_ROWS
    block = 0
    if has_calls:
        block = min(max(_TIMELINE_MIN, min(_TIMELINE_MAX, height // 5)), room - 1)
        # Without room for a junction, a summary and one call, drop the block.
        if block < 3:
            block = 0

    return Layout(
        width=width,
        height=height,
        left_w=left,
        right_w=right,
        body_rows=room - block,
        timeline_block=block,
        degraded=False,
    )


def is_outlier(elapsed_ms: float, baseline_ms: float) -> bool:
    """Whether this call is slow against the baseline; both thresholds must hold."""
    if baseline_ms <= 0:
        return False
    return (
        elapsed_ms >= baseline_ms * OUTLIER_RATIO
        and elapsed_ms - baseline_ms >= OUTLIER_MIN_DELTA_MS
    )


def water_bar(elapsed_ms: float, *, scale_ms: float, cells: int) -> str:
    """Draw a duration as a bar ``cells`` columns wide.

    Sub-cell precision comes from the eighth-block characters; whole-cell scaling
    would make 12 ms and 13 ms the same length. A successful call always gets at
    least 1/8 of a cell: a zero-length bar is indistinguishable from ``✗``.
    """
    if cells <= 0 or scale_ms <= 0 or elapsed_ms <= 0:
        return ""

    if use_ascii():
        filled = max(1, min(cells, round(elapsed_ms / scale_ms * cells)))
        return _BAR_FULL_ASCII * filled

    eighths = max(1, round(min(1.0, elapsed_ms / scale_ms) * cells * 8))
    full, part = divmod(eighths, 8)
    bar = _BAR_FULL * full
    if part:
        bar += _BAR_PARTIALS[part - 1]
    return bar[:cells]


# ---------------------------------------------------------------- entry point


def build_view(state: TuiState, *, width: int, height: int) -> list[Text]:
    """Draw the state as one screen: exactly ``height`` lines of exactly
    ``width`` columns.
    """
    if height <= 0:
        return []
    if width <= 0:
        return [Text("") for _ in range(height)]

    lay = layout_of(width, height, has_calls=bool(state.calls))
    if lay.degraded:
        return _degraded_view(state, width=width, height=height)

    lines = [
        _top_border(state, lay),
        _identity_line(state, lay),
        _pane_junction(state, lay),
    ]
    lines.extend(_body(state, lay))
    if lay.has_timeline:
        lines.append(_timeline_junction(lay))
        lines.extend(_timeline(state, lay))
    lines.append(_bottom_border(lay))
    lines.append(_status_bar(state, width=width))
    return lines


def _degraded_view(state: TuiState, *, width: int, height: int) -> list[Text]:
    """Terminal too small: keep the status bar and blank the rest.

    Drawing a frame that does not fit produces broken lines.
    """
    lines = [Text(" " * width) for _ in range(height)]
    lines[-1] = _status_bar(state, width=width)
    return lines


# ---------------------------------------------------------------- borders


def _box() -> rich_box.Box:
    """Where the border characters come from. Falls back under ``MCPDUMP_ASCII``:
    on a terminal that cannot draw Unicode borders, misaligned blocks read far
    worse than ``+--+``.
    """
    return rich_box.ASCII if use_ascii() else rich_box.SQUARE


def _fit(line: Text, width: int) -> Text:
    """Trim to exactly ``width`` columns: ellipsis when too long, padding when short.

    ``width`` of 0 needs its own guard: ``Text.truncate(0, overflow="ellipsis")``
    reaches ``set_cell_size(plain, -1)``, a negative width.
    """
    if width <= 0:
        return Text("")
    line.truncate(width, overflow="ellipsis", pad=True)
    return line


def _tail(text: str, width: int) -> str:
    """Keep the last ``width`` columns.

    With the cursor at the end, the user needs the final characters;
    ``truncate`` cuts the tail, which is exactly what they just typed.
    """
    if width <= 0:
        return ""
    out = ""
    used = 0
    for ch in reversed(text):
        step = char_width(ch)
        if used + step > width:
            break
        out = ch + out
        used += step
    return out


def _seg(label: str, width: int, *, style: str) -> Text:
    """One segment of a junction line: a label, then a horizontal rule to fill."""
    text = Text()
    inner = f" {label} "
    if width <= 0:
        return text
    if display_width(inner) >= width:
        return _fit(Text(truncate(inner, width), style=style), width)
    text.append(inner, style=style)
    text.append(_box().row_horizontal * (width - display_width(inner)), style=_DIM)
    return text


def _top_border(state: TuiState, lay: Layout) -> Text:
    b = _box()
    title = f" {t('tui.pane.server')} "
    inner = lay.width - 2
    line = Text(b.top_left, style=_DIM)
    if display_width(title) >= inner:
        line.append(b.top * inner, style=_DIM)
    else:
        fill = inner - display_width(title)
        line.append(b.top * (fill // 2), style=_DIM)
        line.append(title, style=_LABEL)
        line.append(b.top * (fill - fill // 2), style=_DIM)
    line.append(b.top_right, style=_DIM)
    return _fit(line, lay.width)


def _pane_junction(state: TuiState, lay: Layout) -> Text:
    """The junction below the title, carrying both pane names; the focused one
    is highlighted.
    """
    b = _box()
    tools = visible_tools(state)
    left = _seg(
        t("tui.pane.tools_count", count=len(tools))
        if state.tools
        else t("tui.pane.tools"),
        lay.left_w - 1,
        style=_BRAND if state.pane is Pane.TOOLS else _DIM,
    )
    right = _seg(
        _wire_label(state),
        lay.right_w - 1,
        style=_BRAND if state.pane is Pane.WIRE else _DIM,
    )
    line = Text(b.row_left, style=_DIM)
    line.append(b.row_horizontal, style=_DIM)
    line.append_text(left)
    line.append(b.top_divider, style=_DIM)
    line.append(b.row_horizontal, style=_DIM)
    line.append_text(right)
    line.append(b.row_right, style=_DIM)
    return _fit(line, lay.width)


def _wire_label(state: TuiState) -> str:
    """The message-view pane label. Scrolled back it becomes ``4/6``; without a
    position there is no way to tell where you are.
    """
    total = len(state.frames)
    if total <= 0:
        return t("tui.pane.wire")
    if state.wire_offset > 0:
        return t("tui.pane.wire_offset", index=total - state.wire_offset, count=total)
    return t("tui.pane.wire_count", count=total)


def _timeline_junction(lay: Layout) -> Text:
    b = _box()
    line = Text(b.row_left, style=_DIM)
    line.append(b.row_horizontal * lay.left_w, style=_DIM)
    line.append(b.bottom_divider, style=_DIM)
    line.append(b.row_horizontal * lay.right_w, style=_DIM)
    line.append(b.row_right, style=_DIM)
    return _fit(line, lay.width)


def _bottom_border(lay: Layout) -> Text:
    """The bottom edge. The junction is ``┴`` only when a divider meets it from above."""
    b = _box()
    line = Text(b.bottom_left, style=_DIM)
    if lay.has_timeline:
        line.append(b.bottom * (lay.width - 2), style=_DIM)
    else:
        line.append(b.bottom * lay.left_w, style=_DIM)
        line.append(b.bottom_divider, style=_DIM)
        line.append(b.bottom * lay.right_w, style=_DIM)
    line.append(b.bottom_right, style=_DIM)
    return _fit(line, lay.width)


# ---------------------------------------------------------------- identity line


def _identity_line(state: TuiState, lay: Layout) -> Text:
    """The server's description on one line. Values are separated by ``·`` with no
    field names: the line confirms the right server was reached, nothing more.
    """
    b = _box()
    inner = lay.width - 2
    line = Text(b.mid_left, style=_DIM)
    body = Text(" ")
    body.append_text(_identity_body(state))
    line.append_text(_fit(body, inner))
    line.append(b.mid_right, style=_DIM)
    return _fit(line, lay.width)


def _identity_body(state: TuiState) -> Text:
    server = state.server
    if server is None:
        return Text(t("tui.connecting"), style=_DIM)

    body = Text(server.name or t("render.unnamed"), style=_BRAND)
    if server.version:
        # The version follows the name with no separator: it is part of the name.
        body.append(f" v{server.version}", style=_DIM)
    for value, style in (
        (state.transport, _META),
        (server.protocol_version, _DIM),
        (" · ".join(server.describe_capabilities()), _META),
    ):
        if value:
            body.append(" · ", style=_DIM)
            body.append(value, style=style)
    return body


# ---------------------------------------------------------------- body columns


def _body(state: TuiState, lay: Layout) -> list[Text]:
    b = _box()
    left = _tools_pane(state, width=lay.left_w, height=lay.body_rows)
    right = _wire_pane(state, width=lay.right_w, height=lay.body_rows)
    lines: list[Text] = []
    # Both columns must be the same height or lines come out half-filled;
    # ``strict`` asserts it.
    for left_row, right_row in zip(left, right, strict=True):
        line = Text(b.mid_left, style=_DIM)
        line.append_text(left_row)
        line.append(b.mid_vertical, style=_DIM)
        line.append_text(right_row)
        line.append(b.mid_right, style=_DIM)
        lines.append(_fit(line, lay.width))
    return lines


def _tools_pane(state: TuiState, *, width: int, height: int) -> list[Text]:
    rows: list[Text] = []
    if state.mode is Mode.FILTER or state.filter_text:
        rows.append(_filter_row(state, width=width))

    tools = visible_tools(state)
    slots = height - len(rows)
    if not tools:
        message = t("tui.no_tools_filtered") if state.tools else t("tui.no_tools")
        rows.append(_fit(Text(" " + message, style=_DIM), width))
    elif slots > 0:
        start, end = _window(len(tools), state.selected_tool, slots)
        for index in range(start, end):
            rows.append(
                _tool_row(
                    tools[index],
                    width=width,
                    selected=index == state.selected_tool,
                )
            )
    return _pad_rows(rows, width=width, height=height, align="top")


def _filter_row(state: TuiState, *, width: int) -> Text:
    """Echo the filter text: with a cursor in ``FILTER`` mode, as a hint in ``BROWSE``.

    A long filter keeps its tail rather than its head, where the user is typing.
    """
    cursor = _CURSOR_ASCII if use_ascii() else _CURSOR
    line = Text(" /", style=_DIM)
    budget = width - display_width(line.plain)
    if state.mode is Mode.FILTER:
        line.append(_tail(state.filter_text, max(0, budget - 1)), style=_TYPE)
        line.append(cursor, style=_BRAND)
    else:
        line.append(truncate(state.filter_text, max(0, budget)), style=_TYPE)
    return _fit(line, width)


def _tool_row(tool: ToolEntry, *, width: int, selected: bool) -> Text:
    row = Text()
    row.append(f"{SYMBOLS.selected} " if selected else "  ",
               style=_BRAND if selected else _DIM)
    name_w = max(8, min(20, width * 2 // 5))
    desc_w = width - 2 - name_w - 2
    row.append(ljust(truncate(tool.name, name_w), name_w),
               style=_BRAND if selected else "")
    if desc_w >= 8:
        desc = (tool.description or tool.title or "").strip().replace("\n", " ")
        row.append("  ")
        row.append(ljust(truncate(desc, desc_w), desc_w), style=_DIM)
    return _fit(row, width)


def _window(total: int, selected: int, size: int) -> tuple[int, int]:
    """The visible ``[start, end)``, with the selection kept roughly centred:
    scrolling flush against an edge hides what is above or below.
    """
    if size <= 0 or total <= 0:
        return 0, 0
    if total <= size:
        return 0, total
    start = min(max(0, selected - size // 2), total - size)
    return start, start + size


# ---------------------------------------------------------------- message view


def _wire_pane(state: TuiState, *, width: int, height: int) -> list[Text]:
    if not state.frames:
        return _placeholder(t("tui.no_frames"), width=width, height=height)

    cursor = max(0, _cursor_index(state))
    expanded = _expanded_entry(state)
    if expanded is not None:
        return _pad_rows(
            _expanded_rows(expanded, width=width, height=height, cursor=True),
            width=width,
            height=height,
        )

    anchor = _frame_rows(state.frames[cursor], width=width, cursor=True)
    below, _ = _stack_down(state.frames, cursor + 1, height, len(anchor), width)
    above, older = _stack_up(
        state.frames, cursor - 1, height, len(anchor) + len(below), width
    )
    return _pad_rows(above + anchor + below, width=width, height=height, older=older)


def _stack_down(
    frames: tuple[WireEntry, ...], start: int, height: int, used: int, width: int
) -> tuple[list[Text], int]:
    """How many rows fit below the anchor (the newer frames).

    The half-height ceiling is deliberate: without it the anchor drifts to the
    top of the pane as you scroll back, leaving no room above for the older
    frames.
    """
    rows: list[Text] = []
    index = start
    ceiling = max(0, (height - used) // 2)
    while index < len(frames):
        group = _frame_rows(frames[index], width=width, cursor=False)
        if len(rows) + len(group) > ceiling:
            break
        rows = rows + group
        index += 1
    return rows, index


def _stack_up(
    frames: tuple[WireEntry, ...], start: int, height: int, used: int, width: int
) -> tuple[list[Text], int]:
    """Rows above the anchor (the older frames), plus the index of the first
    frame that did not fit.
    """
    rows: list[Text] = []
    index = start
    while index >= 0:
        group = _frame_rows(frames[index], width=width, cursor=False)
        if used + len(group) > height:
            break
        rows = group + rows
        used += len(group)
        index -= 1
    return rows, index + 1


def _cursor_index(state: TuiState) -> int:
    """Index of the frame ``wire_offset`` points at, counting back from the newest."""
    index = len(state.frames) - 1 - state.wire_offset
    return index if 0 <= index < len(state.frames) else -1


def _expanded_entry(state: TuiState) -> WireEntry | None:
    """Expansion tracks a sequence number, not an index: once the frame cap
    starts dropping the oldest, indices shift.
    """
    if state.expanded_seq is None:
        return None
    for entry in state.frames:
        if entry.seq == state.expanded_seq:
            return entry
    return None


def _frame_rows(entry: WireEntry, *, width: int, cursor: bool) -> list[Text]:
    rows = [_frame_header(entry, width=width, cursor=cursor)]
    payload = entry.frame.payload
    if payload is not None:
        budget = max(1, width - display_width(PAYLOAD_INDENT))
        rows.append(
            _fit(Text(PAYLOAD_INDENT + truncate(payload, budget), style=_DIM), width)
        )
    return rows


def _frame_header(entry: WireEntry, *, width: int, cursor: bool) -> Text:
    frame = entry.frame
    text = Text()
    text.append(SYMBOLS.selected if cursor else " ", style=_BRAND if cursor else _DIM)
    text.append(" ")
    text.append(frame.symbol, style=frame.style)
    text.append(" ")
    text.append(frame.label, style=frame.style)
    if frame.elapsed_ms is not None:
        right = format_duration(frame.elapsed_ms)
        gap = width - display_width(text.plain) - display_width(right)
        if gap >= 1:
            text.append(" " * gap)
            text.append(right, style=_META)
    return _fit(text, width)


def _expanded_rows(
    entry: WireEntry, *, width: int, height: int, cursor: bool
) -> list[Text]:
    """Expanding is a magnifier, not two extra rows: the pane draws this one frame
    with the whole payload wrapped, which says more than a few extra list rows
    where long JSON is cut off either way.
    """
    rows = [_frame_header(entry, width=width, cursor=cursor)]
    payload = entry.frame.payload
    if payload is None:
        return rows
    budget = max(1, width - display_width(PAYLOAD_INDENT))
    for line in _wrap(payload, budget, max(0, height - 1)):
        rows.append(_fit(Text(PAYLOAD_INDENT + line, style=_DIM), width))
    return rows


def _wrap(text: str, width: int, limit: int) -> list[str]:
    """Wrap by display width, at most ``limit`` lines.

    Split on ``\\n`` first: ``char_width("\\n")`` is 0, so without the split the
    newlines count as zero-width and the whole JSON collapses onto one line.
    """
    if width <= 0 or limit <= 0:
        return []
    out: list[str] = []
    for raw in text.splitlines() or [""]:
        current = ""
        used = 0
        for ch in raw:
            step = char_width(ch)
            if used + step > width and current:
                out.append(current)
                if len(out) >= limit:
                    return out
                current, used = "", 0
            current += ch
            used += step
        out.append(current)
        if len(out) >= limit:
            return out
    return out


# ---------------------------------------------------------------- timeline


def _timeline(state: TuiState, lay: Layout) -> list[Text]:
    calls = state.calls
    ok = [record.elapsed_ms for record in calls if record.ok]
    baseline = _median(ok)

    rows = [_fit(Text(" " + _timeline_summary(calls, ok), style=_DIM), lay.width)]

    slots = lay.call_rows
    if slots > 0:
        end = len(calls) - state.timeline_offset
        start = max(0, end - slots)
        shown = calls[start:end]
        scale = max((record.elapsed_ms for record in shown if record.ok), default=0.0)
        rows.extend(
            _call_row(record, width=lay.width, baseline=baseline, scale=scale)
            for record in shown
        )
    return _pad_rows(
        rows, width=lay.width, height=lay.timeline_block - 1, align="top"
    )


def _timeline_summary(calls: Sequence[CallRecord], ok: Sequence[float]) -> str:
    if not ok:
        return t("tui.timeline_empty", count=len(calls))
    return t(
        "tui.calls_summary",
        count=len(calls),
        p50=format_duration(_median(ok)),
        max=format_duration(max(ok)),
    )


def _median(values: Sequence[float]) -> float:
    """The median, not the mean: one 30-second timeout drags the mean up tenfold,
    after which every normal call looks fast.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _call_row(
    record: CallRecord, *, width: int, baseline: float, scale: float
) -> Text:
    """One call: method, bar, duration, marker.

    Failed and timed-out calls get no bar; the reason goes where the bar would
    be. A length would suggest they were merely slow, and slow and unsuccessful
    are investigated differently.
    """
    inner = width - 2
    label_w = min(18, max(6, inner // 5))
    dur_w = min(10, max(6, inner // 6))
    bar_w = max(1, inner - label_w - dur_w - 6)

    outlier = record.ok and is_outlier(record.elapsed_ms, baseline)
    row = Text()
    row.append(" ")
    row.append(ljust(truncate(record.method, label_w), label_w),
               style=_WARN if outlier else _BRAND)
    row.append(" ")

    if record.ok:
        row.append(
            ljust(water_bar(record.elapsed_ms, scale_ms=scale, cells=bar_w), bar_w),
            style=_WARN if outlier else "mcpdump.res",
        )
        row.append(" ")
        row.append(rjust(format_duration(record.elapsed_ms), dur_w), style=_META)
        row.append(" ")
        row.append(SYMBOLS.warning if outlier else " ", style=_WARN)
    else:
        row.append(
            ljust(truncate(record.detail or t("tui.call_failed"), bar_w), bar_w),
            style=_ERR,
        )
        row.append(" ")
        row.append(rjust("—", dur_w), style=_DIM)
        row.append(" ")
        row.append(SYMBOLS.failure, style=_ERR)
    return _fit(row, width)


# ---------------------------------------------------------------- status bar


def _status_bar(state: TuiState, *, width: int) -> Text:
    """The final line. Unframed: it is a key hint, not one of the panes."""
    head = Text()
    head.append(" ")
    head.append(t(_MODE_KEYS[state.mode]), style=_LABEL)
    head.append("  ")

    tail = _status_tail(state)
    tail_w = display_width(tail.plain) if tail is not None else 0
    room = width - display_width(head.plain) - tail_w
    if tail is not None and room < 4:
        tail, tail_w = None, 0
        room = width - display_width(head.plain)

    head.append_text(_status_body(state, budget=max(0, room - 1)))

    if tail is None:
        return _fit(head, width)
    gap = width - display_width(head.plain) - display_width(tail.plain)
    return _fit(head + Text(" " * max(0, gap)) + tail, width)


def _status_body(state: TuiState, *, budget: int) -> Text:
    if state.mode is Mode.ARGS:
        return _args_editor(state, budget=budget)

    body = Text()
    if state.mode is Mode.FILTER:
        body.append("/", style=_DIM)
        body.append(state.filter_text, style=_TYPE)
        body.append(_CURSOR_ASCII if use_ascii() else _CURSOR, style=_BRAND)
        return _fit(body, budget)

    if state.filter_text:
        body.append("/", style=_DIM)
        body.append(state.filter_text, style=_TYPE)
        body.append("  ")
    body.append(t("tui.hint.browse"), style=_DIM)
    return _fit(body, budget)


def _args_editor(state: TuiState, *, budget: int) -> Text:
    """A horizontally scrolled window over the argument line.

    The cursor has to stay inside it: once it slides off the right the user
    cannot see where they are typing. A column is left either side because
    ``_fit``'s ellipsis would otherwise push the cursor out.
    """
    if budget <= 0:
        return Text("")

    buffer = state.args_buffer
    cursor = max(0, min(state.args_cursor, len(buffer)))
    start = cursor
    while start > 0 and display_width(buffer[start - 1:cursor]) <= budget - 2:
        start -= 1

    body = Text()
    body.append(buffer[start:cursor], style=_TYPE)
    body.append(_CURSOR_ASCII if use_ascii() else _CURSOR, style=_BRAND)
    room = budget - display_width(body.plain)
    if room > 0:
        body.append(truncate(buffer[cursor:], room), style=_TYPE)
    return _fit(body, budget)


def _status_tail(state: TuiState) -> Text | None:
    """The right-hand status. Priority: argument problem, call error, busy.

    An argument problem comes first because it is what the user is editing now.
    """
    if state.mode is Mode.ARGS and state.args_problem is not None:
        return Text(_problem_text(state), style=_ERR)
    if state.error_text:
        return Text(_one_line(state.error_text), style=_ERR)
    if state.busy:
        return Text(t("tui.busy"), style=_WARN)
    return None


def _problem_text(state: TuiState) -> str:
    if state.args_problem is ArgsProblem.NOT_JSON:
        return t("tui.problem.not_json")
    if state.args_problem is ArgsProblem.NOT_OBJECT:
        return t("tui.problem.not_object")
    if state.args_problem is ArgsProblem.MISSING_REQUIRED:
        return t("tui.problem.missing_required", name=state.missing_param)
    return ""


def _one_line(text: str) -> str:
    """Exception messages often contain newlines, which would push the status bar
    to two rows and break the layout.
    """
    return " ".join(text.split())


# ---------------------------------------------------------------- helpers


def _pad_rows(
    rows: list[Text],
    *,
    width: int,
    height: int,
    older: int = 0,
    align: str = "bottom",
) -> list[Text]:
    """Pad a group of rows to exactly ``height`` lines.

    ``align`` decides which end the blanks go on, and that is not a style
    choice: the message view is a log, so the newest entry is pinned to the
    bottom, while the tool list and timeline grow from the top.

    ``older`` applies only when bottom-aligned: the hint hangs on the top blank.
    """
    rows = [_fit(row, width) for row in rows[:height]]
    blank = height - len(rows)
    if blank <= 0:
        return rows
    if align == "top":
        return [*rows, *(Text(" " * width) for _ in range(blank))]

    head: list[Text] = []
    if older > 0:
        head.append(_fit(Text(" " + t("tui.older", count=older), style=_DIM), width))
        blank -= 1
    head.extend(Text(" " * width) for _ in range(blank))
    return head + rows


def _placeholder(message: str, *, width: int, height: int) -> list[Text]:
    """Hint for an empty pane, on the middle row where the eye lands anyway."""
    rows = [Text(" " * width) for _ in range(height)]
    if rows and width > 0:
        rows[height // 2] = _fit(Text(" " + message, style=_DIM), width)
    return rows

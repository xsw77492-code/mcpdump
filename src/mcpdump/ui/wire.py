"""Message-flow view: raw JSON-RPC frames rendered as something readable.

Every round trip (an ``Exchange``) becomes two frames; the duration hangs on the
response, since it measures the whole round trip. A frame is a header (direction
symbol, method name, right-aligned duration) plus the payload, truncated to the
display width unless ``full=True``.

``build_wire``/``render_wire`` batch-render for ``call``; ``build_frame``/
``render_frame`` serve ``watch``, which draws as it forwards.

All output is built with ``Text``, never by interpolating Rich markup into an
f-string: ``[`` and ``]`` are ordinary in JSON but markup reads them as tags.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rich.console import Console
from rich.text import Text

from ..core.session import Exchange
from ..i18n import t
from .layout import end_gap
from .render import format_duration
from .symbols import SYMBOLS
from .width import display_width, truncate

__all__ = [
    "Frame",
    "build_frame",
    "build_wire",
    "frames_of",
    "render_frame",
    "render_wire",
    "request_frame",
    "response_frame",
    "summarize",
]

#: Indent for payload lines; the level comes from indentation, not another symbol.
PAYLOAD_INDENT = "  "

_STYLE_REQUEST = "mcpdump.req"
_STYLE_RESPONSE = "mcpdump.res"
_STYLE_ERROR = "mcpdump.err"
_STYLE_PAYLOAD = "mcpdump.dim"
_STYLE_META = "mcpdump.meta"


@dataclass(frozen=True)
class Frame:
    """The renderable form of one frame."""

    symbol: str
    style: str
    label: str
    elapsed_ms: float | None
    payload: str | None

    @property
    def is_error(self) -> bool:
        return self.style == _STYLE_ERROR


def request_frame(method: str, line: str) -> Frame:
    """A request frame. The duration belongs on the response, not here."""
    return Frame(SYMBOLS.request, _STYLE_REQUEST, method, None, line)


def response_frame(
    method: str,
    line: str | None,
    *,
    elapsed_ms: float | None = None,
    error: str | None = None,
) -> Frame:
    """A response frame.

    ``line`` of ``None`` means a timeout or transport failure, drawn explicitly
    rather than skipped. ``elapsed_ms`` of ``None`` means no duration could be
    measured, so none is shown rather than inventing ``0.0 ms``.
    """
    if line is None:
        return Frame(SYMBOLS.failure, _STYLE_ERROR, error or method, elapsed_ms, None)
    return Frame(SYMBOLS.response, _STYLE_RESPONSE, method, elapsed_ms, line)


def _frames_of_one(exchange: Exchange) -> list[Frame]:
    return [
        request_frame(exchange.method, exchange.request_line),
        response_frame(
            exchange.method,
            exchange.response_line,
            elapsed_ms=exchange.elapsed_ms,
            error=exchange.error,
        ),
    ]


def frames_of(exchanges: Sequence[Exchange]) -> list[Frame]:
    """Expand round trips into a frame sequence, two frames each."""
    out: list[Frame] = []
    for exchange in exchanges:
        out.extend(_frames_of_one(exchange))
    return out


def summarize(exchanges: Sequence[Exchange]) -> str:
    """One summary line: round trips, total time, slowest call.

    Returns an empty string with no exchanges.
    """
    if not exchanges:
        return ""
    total = sum(ex.elapsed_ms for ex in exchanges)
    if len(exchanges) == 1:
        return t("wire.summary_single", total=format_duration(total))
    slowest = max(exchanges, key=lambda ex: ex.elapsed_ms)
    mark = SYMBOLS.failure if slowest.error else SYMBOLS.response
    return t(
        "wire.summary_multi",
        count=len(exchanges),
        total=format_duration(total),
        mark=mark,
        method=slowest.method,
        elapsed=format_duration(slowest.elapsed_ms),
    )


def _header(frame: Frame, *, width: int) -> Text:
    text = Text()
    text.append(f"{frame.symbol} ", style=frame.style)
    text.append(frame.label, style=frame.style)
    if frame.elapsed_ms is not None:
        right = format_duration(frame.elapsed_ms)
        text.append(" " * end_gap(text.plain, right, width=width))
        text.append(right, style=_STYLE_META)
    return text


def _payload(frame: Frame, *, width: int, full: bool) -> Text | None:
    if frame.payload is None:
        return None
    budget = max(1, width - display_width(PAYLOAD_INDENT))
    body = frame.payload if full else truncate(frame.payload, budget)
    return Text(PAYLOAD_INDENT + body, style=_STYLE_PAYLOAD)


def build_frame(frame: Frame, *, width: int, full: bool = False) -> list[Text]:
    """Render a single frame to lines.

    The per-frame entry point serves ``watch``, which prints as it forwards and
    cannot batch.
    """
    out = [_header(frame, width=width)]
    payload = _payload(frame, width=width, full=full)
    if payload is not None:
        out.append(payload)
    return out


def build_wire(
    exchanges: Sequence[Exchange],
    *,
    width: int,
    full: bool = False,
    summary: bool = True,
) -> list[Text]:
    """Build the message-flow view as rich text lines.

    Nothing is printed; ``render_wire`` does that, which lets tests assert on
    line content with no Console or terminal-width detection.
    """
    out: list[Text] = []
    for index, exchange in enumerate(exchanges):
        if index:
            out.append(Text())  # Blank line between round trips, for grouping.
        for frame in _frames_of_one(exchange):
            out.extend(build_frame(frame, width=width, full=full))

    if summary:
        line = summarize(exchanges)
        if line:
            out.append(Text())
            out.append(Text(line, style=_STYLE_META))
    return out


def render_frame(
    console: Console,
    frame: Frame,
    *,
    width: int | None = None,
    full: bool = False,
) -> None:
    """Print one frame; every frame from ``watch`` goes through here.

    A frame is one ``print`` call: two calls would let another thread interleave
    a different frame between header and payload.
    """
    columns = width or console.width
    console.print(
        Text("\n").join(build_frame(frame, width=columns, full=full)), soft_wrap=True
    )


def render_wire(
    console: Console,
    exchanges: Sequence[Exchange],
    *,
    width: int | None = None,
    full: bool = False,
    summary: bool = True,
) -> None:
    """Print the message-flow view, using the console width when ``width`` is omitted."""
    if not exchanges:
        return
    columns = width or console.width
    for line in build_wire(exchanges, width=columns, full=full, summary=summary):
        console.print(line, soft_wrap=True)

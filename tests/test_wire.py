"""Unit tests for the wire view.

``build_wire`` returns ``Text`` lines rather than printing, so widths can be asserted
precisely, unaffected by terminal width detection.
"""

from __future__ import annotations

from mcpdump.core.session import Exchange
from mcpdump.ui import SYMBOLS, display_width
from mcpdump.ui.wire import PAYLOAD_INDENT, build_wire, frames_of, summarize

#: A frame with ``[`` ``]``, normal in JSON arrays, that triggers markup injection
MARKUP_PAYLOAD = '{"result":{"content":[{"type":"text","text":"[bold red]hi[/]"}]}}'


def _exchange(
    method: str = "tools/call",
    request: str = '{"jsonrpc":"2.0","id":1,"method":"tools/call"}',
    response: str | None = '{"jsonrpc":"2.0","id":1,"result":{}}',
    elapsed: float = 1.0,
    error: str | None = None,
) -> Exchange:
    return Exchange(method, request, response, elapsed, error)


class TestFramesOf:
    def test_one_exchange_becomes_two_frames(self) -> None:
        frames = frames_of([_exchange()])
        assert [f.symbol for f in frames] == [SYMBOLS.request, SYMBOLS.response]

    def test_elapsed_only_on_response_frame(self) -> None:
        # Elapsed is the whole round trip; putting it on the request frame would be a lie
        request, response = frames_of([_exchange(elapsed=12.3)])
        assert request.elapsed_ms is None
        assert response.elapsed_ms == 12.3

    def test_missing_response_becomes_error_frame(self) -> None:
        _, response = frames_of([_exchange(response=None, error="等待响应超时")])
        assert response.symbol == SYMBOLS.failure
        assert response.label == "等待响应超时"
        assert response.is_error
        assert response.payload is None

    def test_error_frame_falls_back_to_method_name(self) -> None:
        _, response = frames_of([_exchange(response=None, error=None)])
        assert response.label == "tools/call"

    def test_headers_use_semantic_styles(self) -> None:
        request, response = frames_of([_exchange()])
        assert request.style == "mcpdump.req"
        assert response.style == "mcpdump.res"

    def test_error_uses_error_style(self) -> None:
        _, response = frames_of([_exchange(response=None, error="boom")])
        assert response.style == "mcpdump.err"

    def test_empty_input(self) -> None:
        assert frames_of([]) == []


class TestSummarize:
    def test_empty(self) -> None:
        assert summarize([]) == ""

    def test_single_exchange(self) -> None:
        assert summarize([_exchange(elapsed=12.3)]) == "1 round trip · 12.3 ms"

    def test_counts_round_trips_and_totals_time(self) -> None:
        text = summarize([_exchange(elapsed=1.0), _exchange(elapsed=2.5)])
        assert "2 round trips" in text
        assert "3.5 ms" in text

    def test_picks_slowest(self) -> None:
        text = summarize([
            _exchange("initialize", elapsed=1.0),
            _exchange("tools/call", elapsed=20.0),
        ])
        assert "tools/call 20.0 ms" in text

    def test_marks_slowest_as_failure_when_it_errored(self) -> None:
        text = summarize([
            _exchange("initialize", elapsed=1.0),
            _exchange("tools/call", elapsed=20.0, response=None, error="超时"),
        ])
        assert SYMBOLS.failure in text


class TestBuildWire:
    def test_request_header_has_no_elapsed(self) -> None:
        lines = build_wire([_exchange()], width=60, summary=False)
        header = lines[0].plain
        assert header.startswith(f"{SYMBOLS.request} tools/call")
        assert "ms" not in header

    def test_response_header_right_aligns_elapsed(self) -> None:
        lines = build_wire([_exchange(elapsed=12.3)], width=60, summary=False)
        header = lines[2].plain
        assert header.endswith("12.3 ms")
        assert display_width(header) == 60

    def test_header_never_loses_elapsed_when_width_is_tiny(self) -> None:
        # With too little room, overflow the line rather than drop the elapsed time
        lines = build_wire([_exchange(elapsed=12.3)], width=8, summary=False)
        assert lines[2].plain.endswith("12.3 ms")

    def test_payload_is_indented(self) -> None:
        lines = build_wire([_exchange(request="REQ")], width=60, summary=False)
        assert lines[1].plain == PAYLOAD_INDENT + "REQ"

    def test_payload_is_truncated_to_width(self) -> None:
        long_payload = '{"x":"' + "中" * 100 + '"}'
        lines = build_wire([_exchange(request=long_payload)], width=40, summary=False)
        payload = lines[1].plain
        assert payload.endswith("…")
        assert display_width(payload) <= 40

    def test_full_mode_keeps_payload_intact(self) -> None:
        long_payload = '{"x":"' + "中" * 100 + '"}'
        lines = build_wire([_exchange(request=long_payload)], width=40, full=True, summary=False)
        assert lines[1].plain == PAYLOAD_INDENT + long_payload

    def test_error_frame_has_no_payload_line(self) -> None:
        lines = build_wire([_exchange(response=None, error="超时")], width=60, summary=False)
        # Request frame has a payload (3 lines: header + payload + error header); error has none
        assert len(lines) == 3
        assert lines[-1].plain.startswith(f"{SYMBOLS.failure} 超时")

    def test_blank_line_between_exchanges(self) -> None:
        lines = build_wire([_exchange(), _exchange()], width=60, summary=False)
        plains = [line.plain for line in lines]
        assert plains.count("") == 1
        assert len(plains) == 9  # 2 round trips x 4 lines + 1 separator line

    def test_summary_is_last_line(self) -> None:
        lines = build_wire([_exchange(elapsed=12.3)], width=60)
        assert lines[-1].plain == "1 round trip · 12.3 ms"
        assert lines[-2].plain == ""

    def test_summary_can_be_disabled(self) -> None:
        lines = build_wire([_exchange()], width=60, summary=False)
        assert len(lines) == 4


class TestMarkupSafety:
    """Brackets in a frame are normal (JSON arrays). They must be shown verbatim."""

    def test_brackets_are_shown_literally(self) -> None:
        lines = build_wire([_exchange(response=MARKUP_PAYLOAD)], width=200, summary=False)
        joined = "\n".join(line.plain for line in lines)
        assert "[bold red]hi[/]" in joined

    def test_no_markup_style_leaks_into_spans(self) -> None:
        lines = build_wire([_exchange(response=MARKUP_PAYLOAD)], width=200, summary=False)
        styles = {str(span.style) for line in lines for span in line.spans}
        assert "bold red" not in styles

    def test_only_semantic_styles_are_used(self) -> None:
        lines = build_wire([_exchange(response=MARKUP_PAYLOAD)], width=200, summary=False)
        styles = {str(span.style) for line in lines for span in line.spans}
        assert styles <= {
            "mcpdump.req",
            "mcpdump.res",
            "mcpdump.err",
            "mcpdump.dim",
            "mcpdump.meta",
            "none",
        }

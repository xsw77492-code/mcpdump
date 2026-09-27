"""Tests for ``services/mocker.py``.

The input is constructed frames, not real files: ``MockScript`` only takes a sequence of
``RecordedFrame``, so this covers every branch without a subprocess per case.
"""

from __future__ import annotations

import json

import pytest

from mcpdump.services.mocker import (
    METHOD_NOT_FOUND,
    MockError,
    MockScript,
    UnmatchedRequest,
)
from mcpdump.services.recorder import (
    DIRECTION_TO_CLIENT,
    DIRECTION_TO_SERVER,
    RecordedFrame,
)


def _frame(
    seq: int,
    direction: str,
    line: str,
    *,
    method: str = "",
    at_ms: float = 0.0,
    elapsed_ms: float | None = None,
) -> RecordedFrame:
    """Build one frame. ``method`` defaults to empty: a response has no such field."""
    return RecordedFrame(
        seq=seq,
        at_ms=at_ms,
        direction=direction,
        method=method,
        elapsed_ms=elapsed_ms,
        line=line,
    )


def _request(msg_id: object, method: str, **params: object) -> str:
    payload: dict[str, object] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params:
        payload["params"] = params
    return json.dumps(payload)


def _notification(method: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "method": method})


def _response(msg_id: object, result: object) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error_response(msg_id: object, code: int, message: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
    )


def _round_trip(
    msg_id: object,
    method: str,
    result: object,
    *,
    start_seq: int = 1,
    params: dict[str, object] | None = None,
) -> list[RecordedFrame]:
    """Build one full round trip: a request frame plus a response frame."""
    return [
        _frame(
            start_seq,
            DIRECTION_TO_SERVER,
            _request(msg_id, method, **(params or {})),
            method=method,
        ),
        _frame(start_seq + 1, DIRECTION_TO_CLIENT, _response(msg_id, result)),
    ]


def _handshake() -> list[RecordedFrame]:
    return _round_trip(
        1,
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "t"}},
    )


class TestBuild:
    """Building the answer book from frames."""

    def test_a_round_trip_becomes_an_answer(self) -> None:
        script = MockScript.from_frames(_handshake())
        assert script.methods == ["initialize"]

    def test_several_methods_are_all_registered(self) -> None:
        frames = _handshake() + _round_trip(2, "tools/list", {"tools": []}, start_seq=3)
        script = MockScript.from_frames(frames)
        assert script.methods == ["initialize", "tools/list"]

    def test_a_notification_does_not_become_a_method(self) -> None:
        """A notification has no id, so there is nothing to pair it with and it must not reach
        the answer book.
        """
        frames = [
            _frame(
                1,
                DIRECTION_TO_SERVER,
                _notification("notifications/initialized"),
                method="notifications/initialized",
            )
        ]
        script = MockScript.from_frames(frames)
        assert script.methods == []

    def test_an_orphan_response_is_not_an_answer(self) -> None:
        """A response with no matching request belongs to no method; keeping it would leak it
        out at the wrong moment.
        """
        frames = [_frame(1, DIRECTION_TO_CLIENT, _response(99, {"x": 1}))]
        script = MockScript.from_frames(frames)
        assert script.methods == []

    def test_the_same_method_twice_keeps_both_answers(self) -> None:
        """Two ``tools/call`` with different arguments must both stay: dropping one hands the
        client the wrong result.
        """
        frames = _round_trip(1, "tools/call", {"n": 1})
        frames += _round_trip(2, "tools/call", {"n": 2}, start_seq=3)
        script = MockScript.from_frames(frames)
        assert len(script.replies["tools/call"]) == 2

    def test_an_error_response_is_kept_and_marked(self) -> None:
        """An error the server returned is still a real answer: it goes back verbatim, not
        quietly dropped.
        """
        frames = [
            _frame(1, DIRECTION_TO_SERVER, _request(1, "tools/call"), method="tools/call"),
            _frame(2, DIRECTION_TO_CLIENT, _error_response(1, -32000, "boom")),
        ]
        script = MockScript.from_frames(frames)
        assert script.replies["tools/call"][0].is_error is True

    def test_order_follows_first_appearance(self) -> None:
        frames = _round_trip(1, "z/last", {})
        frames += _round_trip(2, "a/first", {}, start_seq=3)
        assert MockScript.from_frames(frames).methods == ["z/last", "a/first"]


class TestHandshake:
    """``require_handshake``: a recording with no handshake refuses to start."""

    def test_a_recording_with_initialize_passes(self) -> None:
        MockScript.from_frames(_handshake()).require_handshake()

    def test_a_recording_without_initialize_is_refused(self) -> None:
        script = MockScript.from_frames(_round_trip(1, "tools/list", {"tools": []}))
        with pytest.raises(MockError) as excinfo:
            script.require_handshake()
        # The error must say what the recording has, not leave the user guessing.
        assert "initialize" in str(excinfo.value)
        assert "tools/list" in str(excinfo.value)

    def test_the_refusal_suggests_how_to_fix_it(self) -> None:
        script = MockScript.from_frames(_round_trip(1, "tools/list", {"tools": []}))
        with pytest.raises(MockError) as excinfo:
            script.require_handshake()
        assert "mcpdump record" in str(excinfo.value)


class TestAnswer:
    """``answer``: whatever the client sends gets answered."""

    def test_the_id_is_rewritten_to_the_clients_own(self) -> None:
        """The recorded id differs from this client's own, so it must be rewritten."""
        script = MockScript.from_frames(_handshake())
        out = script.answer(_request(777, "initialize"))
        assert out is not None
        assert json.loads(out)["id"] == 777

    def test_a_zero_id_is_rewritten_not_dropped(self) -> None:
        """``id: 0`` is a legal JSON-RPC identifier; a truthiness test would treat it as absent.
        """
        script = MockScript.from_frames(_handshake())
        out = script.answer(_request(0, "initialize"))
        assert out is not None
        assert json.loads(out)["id"] == 0

    def test_a_string_id_survives(self) -> None:
        script = MockScript.from_frames(_handshake())
        out = script.answer(_request("abc-1", "initialize"))
        assert out is not None
        assert json.loads(out)["id"] == "abc-1"

    def test_the_result_is_kept_when_only_the_id_changes(self) -> None:
        script = MockScript.from_frames(_handshake())
        out = script.answer(_request(555, "initialize"))
        assert out is not None
        assert json.loads(out)["result"]["protocolVersion"] == "2025-06-18"

    def test_an_unknown_method_gets_a_proper_jsonrpc_error(self) -> None:
        """Do not fake success: a silent empty result would let the client carry on under a
        false premise.
        """
        script = MockScript.from_frames(_handshake())
        out = script.answer(_request(1, "tools/call"))
        assert out is not None
        payload = json.loads(out)
        assert payload["error"]["code"] == METHOD_NOT_FOUND
        assert payload["id"] == 1

    def test_an_unknown_method_names_itself(self) -> None:
        script = MockScript.from_frames(_handshake())
        out = script.answer(_request(1, "resources/list"))
        assert out is not None
        assert "resources/list" in json.loads(out)["error"]["message"]

    def test_a_notification_gets_no_response_at_all(self) -> None:
        """A notification must get no response; a reply would hand the client something it is
        not waiting for.
        """
        script = MockScript.from_frames(_handshake())
        assert script.answer(_notification("notifications/initialized")) is None

    def test_a_notification_is_counted_separately_from_unmatched(self) -> None:
        """A notification is normal and an unmatched request is not; mixing the two would make
        every session look broken.
        """
        script = MockScript.from_frames(_handshake())
        script.answer(_notification("notifications/initialized"))
        assert script.stats.seen_notifications == 1
        assert script.stats.unmatched == 0

    def test_a_request_without_a_method_is_an_invalid_request(self) -> None:
        """An id but no method is not a notification and not recognised, so it answers as an
        invalid request.
        """
        script = MockScript.from_frames(_handshake())
        out = script.answer(json.dumps({"jsonrpc": "2.0", "id": 1}))
        assert out is not None
        assert json.loads(out)["error"]["code"] == -32600

    def test_an_unparsable_line_is_treated_as_a_notification(self) -> None:
        """A broken line yields no id, so there is nobody to reply to: no reply."""
        script = MockScript.from_frames(_handshake())
        assert script.answer("{not json") is None

    def test_the_same_method_cycles_through_its_answers(self) -> None:
        """The same method asked twice must answer twice; failing would make the mock worse
        than the real server.
        """
        frames = _round_trip(1, "tools/call", {"n": 1})
        frames += _round_trip(2, "tools/call", {"n": 2}, start_seq=3)
        script = MockScript.from_frames(frames)

        first = script.answer(_request(10, "tools/call"))
        second = script.answer(_request(11, "tools/call"))
        assert first is not None and second is not None
        assert json.loads(first)["result"] == {"n": 1}
        assert json.loads(second)["result"] == {"n": 2}

    def test_a_single_answer_is_reused_not_consumed(self) -> None:
        """With one answer, three questions must all be answered; erroring once the answer is
        used up is the worst behaviour.
        """
        script = MockScript.from_frames(_handshake())
        for expected_id in (1, 2, 3):
            out = script.answer(_request(expected_id, "initialize"))
            assert out is not None
            assert json.loads(out)["id"] == expected_id

    def test_unmatched_is_counted(self) -> None:
        script = MockScript.from_frames(_handshake())
        script.answer(_request(1, "nope/one"))
        script.answer(_request(2, "nope/two"))
        assert script.stats.unmatched == 2

    def test_an_error_reply_keeps_its_error_shape(self) -> None:
        """The recording holds an error response, and it must stay an error after the id rewrite."""
        frames = [
            _frame(1, DIRECTION_TO_SERVER, _request(1, "tools/call"), method="tools/call"),
            _frame(2, DIRECTION_TO_CLIENT, _error_response(1, -32000, "boom")),
        ]
        script = MockScript.from_frames(frames)
        out = script.answer(_request(42, "tools/call"))
        assert out is not None
        payload = json.loads(out)
        assert payload["id"] == 42
        assert payload["error"]["message"] == "boom"

    def test_a_broken_response_line_never_reaches_the_answers(self) -> None:
        """A response line whose ``id`` cannot be parsed matches no request, so it has to go.

        With no idea which request it belongs to there is no way to produce it later; keeping
        it would only hand some innocent method a frame of garbage.
        """
        frames = [
            _frame(1, DIRECTION_TO_SERVER, _request(1, "m"), method="m"),
            _frame(2, DIRECTION_TO_CLIENT, "{broken"),
        ]
        script = MockScript.from_frames(frames)
        assert script.methods == []
        # When the client asks for that request it gets a clear "no answer", not the garbage line.
        out = script.answer(_request(1, "m"))
        assert out is not None
        assert json.loads(out)["error"]["code"] == METHOD_NOT_FOUND

    def test_the_rewrite_count_is_tracked(self) -> None:
        script = MockScript.from_frames(_handshake())
        script.answer(_request(1234, "initialize"))
        assert script.stats.rewritten_ids == 1


class TestNextReply:
    """The path where ``next_reply`` raises instead of answering."""

    def test_an_unknown_method_raises(self) -> None:
        script = MockScript.from_frames(_handshake())
        with pytest.raises(UnmatchedRequest) as excinfo:
            script.next_reply("nope")
        assert excinfo.value.method == "nope"

    def test_the_exception_carries_the_method_name(self) -> None:
        """The caller needs it to say which method had no answer."""
        script = MockScript.from_frames(_handshake())
        with pytest.raises(UnmatchedRequest) as excinfo:
            script.next_reply("resources/list")
        assert "resources/list" in str(excinfo.value)

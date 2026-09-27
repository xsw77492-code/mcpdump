"""Tests for deterministic replay (``services/replayer.py``).

Replay's output must be a function of the file alone: a notification is not a dangling
request, orphan responses are not dropped, and the pending table has a ceiling.
"""

from __future__ import annotations

import json
from typing import Any

from mcpdump.services.recorder import DIRECTION_TO_CLIENT, DIRECTION_TO_SERVER, RecordedFrame
from mcpdump.services.replayer import (
    MAX_PENDING,
    Step,
    build_result,
    iter_steps,
    pending_requests,
    to_exchanges,
)


def _request(msg_id: Any, method: str, *, params: dict[str, Any] | None = None) -> RecordedFrame:
    line = json.dumps(
        {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}},
        ensure_ascii=False,
    )
    return RecordedFrame(1, 0.0, DIRECTION_TO_SERVER, method, None, line)


def _response(
    msg_id: Any, method: str, *, elapsed: float = 1.0, error: str | None = None
) -> RecordedFrame:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
    if error is None:
        payload["result"] = {}
    else:
        payload["error"] = {"code": -32000, "message": error}
    line = json.dumps(payload, ensure_ascii=False)
    return RecordedFrame(2, elapsed, DIRECTION_TO_CLIENT, method, elapsed, line)


def _notification(method: str) -> RecordedFrame:
    line = json.dumps({"jsonrpc": "2.0", "method": method}, ensure_ascii=False)
    return RecordedFrame(3, 0.0, DIRECTION_TO_SERVER, method, None, line)


# ---------------------------------------------------------------- rebuilding round trips


def test_a_request_and_its_response_become_one_step() -> None:
    """A flat frame sequence is rebuilt into "request → response", keyed by ``id``,
    exactly as the proxy layer does it."""
    frames = [_request(1, "initialize"), _response(1, "initialize")]

    steps = list(iter_steps(frames))

    assert len(steps) == 1
    assert steps[0].method == "initialize"
    assert steps[0].request_line is not None
    assert steps[0].response_line is not None
    assert not steps[0].dangling
    assert not steps[0].orphan


def test_responses_are_matched_by_id_not_by_order() -> None:
    """With two interleaved requests, matching must go by ``id``, not arrival order.

    Matching by order looks correct on serial sessions, until someone sends two requests
    concurrently.
    """
    frames = [
        _request(1, "alpha"),
        _request(2, "beta"),
        _response(2, "beta"),
        _response(1, "alpha"),
    ]

    steps = list(iter_steps(frames))

    assert len(steps) == 2
    # The first step emitted is beta's, because its response arrived first.
    assert steps[0].method == "beta"
    assert json.loads(steps[0].request_line or "{}")["id"] == 2
    assert steps[1].method == "alpha"
    assert json.loads(steps[1].request_line or "{}")["id"] == 1


def test_a_step_carries_the_elapsed_time_from_the_recording() -> None:
    """The duration is reproduced, not measured.

    Recomputing during replay gives a different result every time and would fill the diff of
    two recordings with noise.
    """
    frames = [_request(1, "tools/list"), _response(1, "tools/list", elapsed=42.5)]

    step = next(iter(iter_steps(frames)))

    assert step.elapsed_ms == 42.5


# ---------------------------------------------------------------- notifications


def test_a_notification_is_not_a_dangling_request() -> None:
    """This one guards against a real bug.

    ``notifications/initialized`` has no ``id`` and should have no response; counting it as
    "sent but never answered" made every healthy recording report "1 unanswered request".
    """
    frames = [_notification("notifications/initialized")]

    step = next(iter(iter_steps(frames)))

    assert step.notification
    assert not step.dangling
    assert not step.orphan
    assert pending_requests(frames) == []


def test_a_real_dangling_request_is_still_reported() -> None:
    """The reverse check: a genuinely dangling request must still be reported.

    Without it, the previous test could pass merely because ``dangling`` is always false.
    """
    frames = [_request(1, "tools/call")]

    steps = list(iter_steps(frames))
    assert steps[0].dangling
    assert pending_requests(frames) == [frames[0].line]


def test_a_notification_does_not_swallow_a_later_response() -> None:
    """A notification wedged between two requests must not disturb any pairing."""
    frames = [
        _request(1, "alpha"),
        _notification("notifications/initialized"),
        _request(2, "beta"),
        _response(1, "alpha"),
        _response(2, "beta"),
    ]

    result = build_result(frames)

    assert result.stats.exchanges == 2
    assert result.stats.notifications == 1
    assert result.stats.dangling == 0
    # One round trip emits one step, positioned where the response arrived. The
    # notification sits right after the first frame, so it sorts before both round trips.
    assert result.methods == ["notifications/initialized", "alpha", "beta"]


# ---------------------------------------------------------------- orphans and anomalies


def test_an_orphan_response_is_kept_not_dropped() -> None:
    """A response that matches no request must be emitted as usual.

    It says this response does not belong to any request in the recording — often the problem
    itself (two connections, or a trimmed recording).
    """
    frames = [_response(99, "tools/list")]

    step = next(iter(iter_steps(frames)))

    assert step.orphan
    assert step.request_line is None
    assert step.response_line is not None
    assert not step.dangling, "an orphan is not dangling: it has no request to dangle from"
    assert build_result(frames).stats.orphan == 1


def test_an_orphan_is_not_counted_as_an_exchange() -> None:
    """An orphan is not an exchange. Counting it inflates "how many calls this session
    made" out of thin air."""
    frames = [_response(99, "tools/list")]

    assert build_result(frames).stats.exchanges == 0


def test_a_garbage_line_does_not_crash_the_replay() -> None:
    """A hand-trimmed recording may hold a half line. Replay must skip it and carry on,
    not abandon the whole run."""
    broken = RecordedFrame(1, 0.0, DIRECTION_TO_SERVER, "unknown", None, "{not json")
    frames = [broken, _request(1, "initialize"), _response(1, "initialize")]

    steps = list(iter_steps(frames))

    # The broken frame has no id, so it is treated as a notification — not dangling.
    assert steps[0].notification
    assert steps[1].method == "initialize"


def test_a_very_slow_exchange_is_marked_truncated() -> None:
    """A request and response beyond ``DANGLING_MS`` apart get marked.

    Not an error but worth a look: the connection may have dropped and reconnected, or the
    recording may be two pieces glued together.
    """
    from mcpdump.services.replayer import DANGLING_MS

    request = _request(1, "slow")
    response = RecordedFrame(
        2, DANGLING_MS + 1.0, DIRECTION_TO_CLIENT, "slow", 5.0, json.dumps({"id": 1, "result": {}})
    )

    step = next(iter(iter_steps([request, response])))

    assert step.truncated
    assert build_result([request, response]).stats.truncated == 1


# ---------------------------------------------------------------- ceilings and determinism


def test_the_pending_table_has_a_ceiling() -> None:
    """Thousands of never-answered requests must not blow up memory.

    The ceiling is deliberate: past it the oldest request is forgotten and its response counts
    as an orphan, which matters less than "the server died and nothing after got a response".
    """
    frames = [_request(index, f"method/{index}") for index in range(1, MAX_PENDING + 50)]

    result = build_result(frames)

    assert result.stats.dangling == MAX_PENDING
    assert result.stats.dangling < len(frames)


def test_a_smaller_ceiling_forgets_the_oldest_first() -> None:
    """What gets forgotten is the oldest, not the newest.

    Forgetting the newest throws away "what just happened", the stretch you want during
    diagnosis; with ``max_pending=2`` only the last two requests remain in the table.
    """
    frames = [_request(index, f"m/{index}") for index in range(1, 5)]
    response = RecordedFrame(
        9, 0.0, DIRECTION_TO_CLIENT, "m/1", 1.0, json.dumps({"id": 1, "result": {}})
    )

    steps = list(iter_steps([*frames, response], max_pending=2))

    assert steps[0].orphan, (
        "id=1 was pushed out of the pending table, so its response cannot match a request"
    )
    assert [step.method for step in steps[1:]] == ["m/3", "m/4"]
    assert all(step.dangling for step in steps[1:])


def test_replaying_twice_gives_byte_identical_results() -> None:
    """Two replays must give exactly the same result.

    This catches any "just read the present": re-timing, iterating a set, relying on hash
    randomisation — none of which show up in a single run.
    """
    frames = [
        _request(1, "initialize"),
        _notification("notifications/initialized"),
        _request(2, "tools/list"),
        _response(1, "initialize"),
        _request(3, "tools/call"),
        _response(3, "tools/call", error="boom"),
        _response(2, "tools/list"),
    ]

    first = build_result(list(frames)).to_dict()
    second = build_result(list(frames)).to_dict()

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_step_order_follows_the_file_exactly() -> None:
    """No re-sorting: replay copies the file's order.

    The proxy only guarantees "a response is not earlier than its request", not strict
    alternation, so below ``beta``'s response is emitted before ``alpha``'s.
    """
    frames = [
        _request(1, "alpha"),
        _request(2, "beta"),
        _response(2, "beta"),
        _response(1, "alpha"),
        _notification("notifications/cancelled"),
    ]

    result = build_result(frames)

    assert result.methods == ["beta", "alpha", "notifications/cancelled"]


def test_bytes_counts_the_wire_payload_only() -> None:
    """``bytes`` counts traffic, not file size: only what went over the wire, excluding
    ``seq`` / ``atMs``.
    """
    frames = [_request(1, "tools/list"), _response(1, "tools/list")]

    expected = len(frames[0].line) + len(frames[1].line)

    assert build_result(frames).stats.bytes == expected


def test_an_empty_recording_is_not_an_error() -> None:
    """A zero-frame recording is legal — the server may have crashed at startup,
    having exchanged not a single byte."""
    result = build_result([])

    assert result.steps == []
    assert result.stats.exchanges == 0
    assert result.stats.dangling == 0


# ---------------------------------------------------------------- shaping into ui.wire


def test_to_exchanges_reuses_the_core_dataclass() -> None:
    """What is produced must be ``core.session.Exchange``.

    A same-shaped class in the service layer would not line up with ``ui.wire``'s signature —
    same field names, different type — and a type checker would catch it.
    """
    from mcpdump.core.session import Exchange

    steps = list(iter_steps([_request(1, "initialize"), _response(1, "initialize")]))

    out = to_exchanges(steps)

    assert all(isinstance(item, Exchange) for item in out)
    assert len(out) == 1


def test_an_error_response_carries_its_message() -> None:
    """The reason for the failure must come along; a bare "failed" is not enough. This is the
    field shown in the ``--wire`` view.
    """
    steps = list(iter_steps([_request(1, "boom"), _response(1, "boom", error="tool exploded")]))

    out = to_exchanges(steps)

    assert out[0].error == "tool exploded"


def test_a_dangling_request_becomes_an_exchange_with_no_response() -> None:
    """A dangling request must appear in ``--wire`` too, with ``response_line=None``; otherwise
    "this request was never answered" vanishes from the view.
    """
    steps = list(iter_steps([_request(1, "tools/call")]))

    out = to_exchanges(steps)

    assert len(out) == 1
    assert out[0].response_line is None
    assert out[0].request_line


def test_an_orphan_becomes_an_exchange_with_no_request() -> None:
    """Orphan responses are the same: an empty ``request_line`` holds the place, but
    the frame must **not** be dropped."""
    steps = list(iter_steps([_response(99, "tools/list")]))

    out = to_exchanges(steps)

    assert len(out) == 1
    assert out[0].request_line == ""
    assert out[0].response_line
    assert out[0].error


def test_notifications_are_folded_into_the_flat_wire_list() -> None:
    """A notification shows up in ``--wire`` as an entry with a request and no response.

    ``ui.wire`` only has the "one round trip" shape, so a notification is squeezed into half
    an entry — deliberately, since the view is for a human to scan.
    """
    steps = list(iter_steps([_notification("notifications/initialized")]))

    out = to_exchanges(steps)

    assert len(out) == 1
    assert out[0].response_line is None


def test_step_label_is_the_bare_method_name() -> None:
    """``label`` carries no marker symbols.

    State (orphan / dangling / slow) is rendered by the render layer; a ``?`` prefix in the
    data layer would force every consumer to parse it.
    """
    steps = list(iter_steps([_request(1, "tools/call"), _response(1, "tools/call")]))

    assert steps[0].label == "tools/call"
    assert isinstance(steps[0], Step)

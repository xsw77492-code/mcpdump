"""Turn a session recording into a fake MCP server for a real client to connect to.

``replay`` reads a recording for a person; ``mock`` reads it for a client. The
input is the same file, and the difference is who initiates: ``replay`` walks it
in order, ``mock`` answers as the client asks.

Timing is discarded: replaying frames after their ``atMs`` offsets would
reproduce the recording machine's latency. The match key is the method name, not
the id, because JSON-RPC ids are the initiator's and the response must be
rewritten with the current request's id. A method may arrive several times with
different arguments, so the rule is "the first unused reply for that method".

A request with no reply in the recording gets a JSON-RPC error rather than a
fabricated success, which would let the client carry on from a false premise.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..i18n import t
from .recorder import (
    DIRECTION_TO_CLIENT,
    DIRECTION_TO_SERVER,
    RecordedFrame,
    message_id,
    message_method,
)

__all__ = [
    "METHOD_NOT_FOUND",
    "MockError",
    "MockScript",
    "UnmatchedRequest",
]

#: Standard JSON-RPC code for a method that does not exist, used for requests the
#: recording lacks.
METHOD_NOT_FOUND = -32601


class MockError(ValueError):
    """The recording cannot serve as a script.

    Subclasses ``ValueError`` because the cause is the input data rather than the
    environment, which lets the command layer return a usage error.
    """


class UnmatchedRequest(RuntimeError):
    """The client asked for a method the recording has no answer for.

    Raised with the method name so the caller can choose between replying with a
    JSON-RPC error and aborting the mock.
    """

    def __init__(self, method: str) -> None:
        super().__init__(method)
        self.method = method


@dataclass(frozen=True)
class _Reply:
    """One usable reply: the raw response line, and whether it carries an ``error``."""

    line: str
    is_error: bool


@dataclass
class MockStats:
    """Counters for one mock run.

    ``unmatched`` and ``seen_notifications`` are diagnostic signals, not errors:
    keeping them apart matters, or every normal session would report an unmatched
    request because of ``notifications/initialized``.
    """

    served: int = 0
    unmatched: int = 0
    seen_notifications: int = 0
    rewritten_ids: int = 0


@dataclass
class MockScript:
    """Every "request method to its response" pairing in the recording.

    Built like ``replayer.iter_steps``, with the id as the join key, but
    producing the other artefact: an answer book for a client rather than steps
    for a person. Notifications and dangling requests have no answer, so neither
    appears here.
    """

    replies: dict[str, list[_Reply]] = field(default_factory=dict)
    #: How many replies each method has consumed; wraps around, see ``next_reply``.
    cursor: dict[str, int] = field(default_factory=dict)
    stats: MockStats = field(default_factory=MockStats)

    @classmethod
    def from_frames(cls, frames: list[RecordedFrame]) -> MockScript:
        """Pair a frame sequence into an answer book.

        Whether the recording is usable is checked by ``require_handshake``, so
        that question is answered in one place.
        """
        script = cls()
        pending: dict[Any, str] = {}

        for frame in frames:
            if frame.direction == DIRECTION_TO_SERVER:
                key = message_id(frame.line)
                if key is None:
                    # A notification: no id, and the client is not waiting.
                    continue
                method = message_method(frame.line)
                if method:
                    pending[key] = method
                continue

            if frame.direction != DIRECTION_TO_CLIENT:
                continue

            key = message_id(frame.line)
            # An orphan response joins no method and would surface at the wrong
            # moment, so it is skipped. The ``pop`` default is an empty string to
            # match ``message_method``'s convention.
            method = pending.pop(key, "") if key is not None else ""
            if not method:
                continue
            script.replies.setdefault(method, []).append(
                _Reply(line=frame.line, is_error=frame.is_error_response)
            )

        return script

    @property
    def methods(self) -> list[str]:
        """Methods the recording can answer, in first-appearance order."""
        return list(self.replies)

    def require_handshake(self) -> None:
        """Refuse to start without an answer for ``initialize``.

        Unable to answer it, the client stalls in the handshake and reports
        something unrelated, while the actual cause (a recording of half a
        session) stays invisible.
        """
        if "initialize" not in self.replies:
            methods = ", ".join(self.methods) or t("mock.no_methods")
            raise MockError(t("mock.no_initialize", methods=methods))

    def next_reply(self, method: str) -> _Reply:
        """Take one reply for the method, cycling when it is asked more than once.

        Cycling rather than failing: a client may ask twice through a retry, and
        refusing the second time would make the mock look less stable than a real
        server. A cursor rather than a queue, because replies must stay reusable.
        """
        answers = self.replies.get(method)
        if not answers:
            self.stats.unmatched += 1
            raise UnmatchedRequest(method)
        index = self.cursor.get(method, 0)
        self.cursor[method] = (index + 1) % len(answers)
        return answers[index]

    def answer(self, request_line: str) -> str | None:
        """Produce the response text for a client message, or ``None`` when none is due.

        The id is rewritten to the one in the request: ids belong to the
        initiator, and the client being recorded used different ones.

        A notification returns ``None`` rather than an empty response, since a
        server must not reply to a message without an id.
        """
        request_id = self._request_id(request_line)
        if request_id is None:
            # Either a notification, or a malformed message with no id at all.
            # Neither gets a response; they are counted separately, since
            # notifications are normal and malformed messages are not.
            if message_method(request_line):
                self.stats.seen_notifications += 1
            else:
                self.stats.unmatched += 1
            return None

        method = message_method(request_line)
        if not method:
            return self._error_line(request_id, -32600, "Invalid Request")
        try:
            reply = self.next_reply(method)
        except UnmatchedRequest:
            return self._error_line(
                request_id,
                METHOD_NOT_FOUND,
                f"method not found in recording: {method}",
            )
        return self._rewrite_id(reply.line, request_id)

    # ---- internals ----

    @staticmethod
    def _request_id(line: str) -> Any:
        return message_id(line)

    def _error_line(self, request_id: Any, code: int, message: str) -> str:
        payload = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
        return json.dumps(payload, ensure_ascii=False)

    def _rewrite_id(self, response_line: str, request_id: Any) -> str:
        """Replace the response's ``id`` with the current request's.

        The ``json.loads`` cannot fail: a line only reaches the answer book after
        its id has been parsed. Re-serialising changes field order and whitespace,
        which is acceptable, since JSON order means nothing to the client.
        """
        payload = json.loads(response_line)
        if not isinstance(payload, dict) or payload.get("id") == request_id:
            return response_line
        payload["id"] = request_id
        self.stats.rewritten_ids += 1
        return json.dumps(payload, ensure_ascii=False)

"""Deterministic replay: walk a recording again without starting a process or
touching the network. This is what makes a recording worth attaching to an issue.

The output is a function of the recording alone: durations are not re-measured
(re-timing would differ every run and fill every diff with noise), frames are not
reordered, and an unparseable frame is reported with its line number.

A round trip is rebuilt as the proxy built it, with the id as the join key in a
pending table. That table needs a cap: a recording can hold thousands of requests
nobody answered. A response that matches nothing is marked ``orphan`` and shown.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..i18n import t
from .recorder import DIRECTION_TO_SERVER, message_id, message_method

if TYPE_CHECKING:
    # For the type checker only. Importing at runtime would establish the
    # ``services`` → ``core`` dependency at module load, for annotations alone.
    from ..core.session import Exchange

__all__ = [
    "DANGLING_MS",
    "MAX_PENDING",
    "ReplayResult",
    "ReplayStats",
    "Step",
    "build_result",
    "iter_steps",
    "pending_requests",
    "to_exchanges",
]

#: Cap on tracked in-flight requests, for the same reason as
#: ``core.proxy.MAX_PENDING``: a long session must not grow without bound on a few
#: unanswered requests.
MAX_PENDING = 512

#: A request still unanswered after this long is treated as dangling (ms). A
#: recording that ends normally may have a last request whose response had not
#: arrived yet, so "whatever is left over at the end" is not enough.
DANGLING_MS = 30_000.0


@dataclass(frozen=True)
class Step:
    """One step: a round trip, a notification, or a response that matches no request."""

    index: int
    direction: str
    method: str
    at_ms: float
    elapsed_ms: float | None
    request_line: str | None
    response_line: str | None
    orphan: bool
    truncated: bool
    #: A one-way notification. Not a dangling request: a notification is not
    #: supposed to get a response.
    notification: bool = False

    #: The request went out and no response ever came. This is the state worth reporting.
    @property
    def dangling(self) -> bool:
        return (
            self.request_line is not None
            and self.response_line is None
            and not self.orphan
            and not self.notification
        )

    @property
    def label(self) -> str:
        """The method name as the renderer displays it.

        No marker symbols: orphan, dangling and slow round trip are expressed by
        the renderer, and ``--json`` consumers already have an ``orphan`` field.
        """
        return self.method


@dataclass
class ReplayStats:
    """Replay counters. ``orphan`` and ``dangling`` are diagnostic signals, not errors."""

    exchanges: int = 0
    notifications: int = 0
    orphan: int = 0
    dangling: int = 0
    truncated: int = 0
    bytes: int = 0


@dataclass
class ReplayResult:
    """The replay output. ``steps`` follows the recording's order exactly."""

    steps: list[Step] = field(default_factory=list)
    stats: ReplayStats = field(default_factory=ReplayStats)

    @property
    def methods(self) -> list[str]:
        return [step.method for step in self.steps]

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": [
                {
                    "index": step.index,
                    "direction": step.direction,
                    "method": step.method,
                    "atMs": step.at_ms,
                    "elapsedMs": step.elapsed_ms,
                    "orphan": step.orphan,
                    "notification": step.notification,
                    "truncated": step.truncated,
                }
                for step in self.steps
            ],
            "stats": {
                "exchanges": self.stats.exchanges,
                "notifications": self.stats.notifications,
                "orphan": self.stats.orphan,
                "dangling": self.stats.dangling,
                "truncated": self.stats.truncated,
                "bytes": self.stats.bytes,
            },
        }


def _is_error_line(line: str) -> str | None:
    """If the response carries an ``error``, return its ``message`` for display."""
    try:
        payload = json.loads(line)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        return str(message) if message is not None else "JSON-RPC error"
    return "JSON-RPC error" if "error" in payload else None


def iter_steps(frames: Iterable[Any], *, max_pending: int = MAX_PENDING) -> Iterator[Step]:
    """Turn a frame sequence into a step sequence, as a stream holding only
    unmatched requests.

    ``frames`` is an ``Iterable`` because callers pass the generator from
    ``iter_frames()``. Whatever is left in the pending table when the file ends
    must still be emitted, or a recording of "the client sent ``initialize`` and
    the server crashed" replays as nothing. Leftovers are emitted in request
    order, not by id.
    """
    #: id -> (request line, request at_ms). The deque exists to drop the oldest
    #: key at the cap.
    order: deque[Any] = deque()
    pending: dict[Any, tuple[str, float]] = {}
    index = 0

    for frame in frames:
        if frame.to_server:
            key = message_id(frame.line)
            if key is None:
                # A notification has no id, so no response is coming. Emitted as
                # a step with ``notification=True`` so the statistics do not
                # count it as dangling.
                index += 1
                yield Step(
                    index=index,
                    direction=frame.direction,
                    method=frame.method,
                    at_ms=frame.at_ms,
                    elapsed_ms=None,
                    request_line=frame.line,
                    response_line=None,
                    orphan=False,
                    truncated=False,
                    notification=True,
                )
                continue

            pending[key] = (frame.line, frame.at_ms)
            order.append(key)
            while len(order) > max_pending:
                pending.pop(order.popleft(), None)
            continue

        key = message_id(frame.line)
        # A response with an id that matches no request is an orphan, and is still
        # emitted: it belongs to no request in this recording, which is often the
        # point.
        entry = pending.pop(key, None) if key is not None else None
        if entry is None:
            index += 1
            yield Step(
                index=index,
                direction=frame.direction,
                method=frame.method,
                at_ms=frame.at_ms,
                elapsed_ms=None,
                request_line=None,
                response_line=frame.line,
                orphan=True,
                truncated=False,
            )
            continue

        request_line, request_at = entry
        index += 1
        yield Step(
            index=index,
            direction=frame.direction,
            method=frame.method,
            at_ms=frame.at_ms,
            elapsed_ms=frame.elapsed_ms,
            request_line=request_line,
            response_line=frame.line,
            orphan=False,
            truncated=frame.at_ms - request_at > DANGLING_MS,
        )

    # The remaining requests will never be answered and must appear, or "the
    # server never replied" leaves no trace in the report.
    for key in sorted(pending, key=lambda item: pending[item][1]):
        request_line, request_at = pending[key]
        index += 1
        yield Step(
            index=index,
            direction=DIRECTION_TO_SERVER,
            method=message_method(request_line) or "?",
            at_ms=request_at,
            elapsed_ms=None,
            request_line=request_line,
            response_line=None,
            orphan=False,
            truncated=False,
        )


def pending_requests(frames: Iterable[Any]) -> list[str]:
    """Request lines still unmatched after a full pass: the dangling requests.

    Separate from ``iter_steps`` because a streaming interface has one return
    value. Notifications do not count as dangling, or every normal recording
    would report ``notifications/initialized`` as unanswered.

    The whole generator must be consumed: dangling requests are emitted during
    the closing phase.
    """
    return [step.request_line for step in iter_steps(frames) if step.dangling and step.request_line]


def build_result(frames: list[Any]) -> ReplayResult:
    """Run the whole recording and return steps with counters, for the places
    that need random access (the report, ``--json``).

    ``bytes`` counts message text only, without metadata: "how big is this
    recording" means traffic, not file size.
    """
    result = ReplayResult()
    for step in iter_steps(frames):
        result.steps.append(step)
        if step.orphan:
            result.stats.orphan += 1
        if step.notification:
            result.stats.notifications += 1
        if step.dangling:
            result.stats.dangling += 1
        if step.truncated:
            result.stats.truncated += 1
        if step.response_line is not None and not step.orphan:
            result.stats.exchanges += 1
        result.stats.bytes += len(step.request_line or "") + len(step.response_line or "")
    return result


def to_exchanges(steps: Iterable[Step]) -> list[Exchange]:
    """Convert steps into ``core.session.Exchange`` for ``ui.wire`` to render.

    The ``core`` dataclass is reused rather than a same-shaped one defined here,
    so ``ui.wire``'s signature matches what is passed.

    An orphan response has no request line, so an empty string stands in and
    ``error`` is set rather than dropping the frame.
    """
    from ..core.session import Exchange  # noqa: F401  (needed at runtime by the calls below)

    out: list[Exchange] = []
    for step in steps:
        if step.orphan:
            out.append(
                Exchange(
                    method=step.method,
                    request_line="",
                    response_line=step.response_line,
                    elapsed_ms=step.elapsed_ms or 0.0,
                    error=t("replay.orphan_mark"),
                )
            )
            continue
        if step.dangling:
            out.append(
                Exchange(
                    method=step.method,
                    request_line=step.request_line or "",
                    response_line=None,
                    elapsed_ms=0.0,
                    error=step.method,
                )
            )
            continue
        out.append(
            Exchange(
                method=step.method,
                request_line=step.request_line or "",
                response_line=step.response_line,
                elapsed_ms=step.elapsed_ms or 0.0,
                error=_is_error_line(step.response_line) if step.response_line else None,
            )
        )
    return out

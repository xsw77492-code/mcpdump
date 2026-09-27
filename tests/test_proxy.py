"""Behaviour tests for the transparent proxy (``core/proxy.py``).

The peer is ``line_echo_server.py``, which knows nothing about MCP, so proxy bugs cannot be
confused with server bugs. stdin / stdout are swapped for ``BytesIO`` stand-ins.
"""

from __future__ import annotations

import io
import sys
import threading
from typing import Any

import pytest

from conftest import ServerCommand
from mcpdump.core.proxy import Direction, ProxyEvent, StdioProxy


def _request(msg_id: int, method: str) -> bytes:
    return f'{{"jsonrpc":"2.0","id":{msg_id},"method":"{method}","params":{{}}}}\n'.encode()


class _Stream:
    """Stand-ins for ``sys.stdin`` / ``sys.stdout``: the proxy only uses ``.buffer``."""

    def __init__(self, raw: bytes = b"") -> None:
        self.buffer = io.BytesIO(raw)


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    server: str,
    incoming: bytes,
    **kwargs: Any,
) -> tuple[StdioProxy, _Stream, list[ProxyEvent]]:
    """Wire up the stand-ins and build the proxy but do not start it, leaving room for cases
    that drive the threads themselves.
    """
    events: list[ProxyEvent] = []
    stdout = _Stream()
    monkeypatch.setattr(sys, "stdin", _Stream(incoming))
    monkeypatch.setattr(sys, "stdout", stdout)
    kwargs.setdefault("on_event", events.append)
    return StdioProxy(server, **kwargs), stdout, events


def _run(
    monkeypatch: pytest.MonkeyPatch,
    server: str,
    incoming: bytes,
    **kwargs: Any,
) -> tuple[int, bytes, list[ProxyEvent], StdioProxy]:
    proxy, stdout, events = _prepare(monkeypatch, server, incoming, **kwargs)
    code = proxy.run()
    proxy.close()
    return code, stdout.buffer.getvalue(), events, proxy


# ---------------------------------------------------------------- byte fidelity


def test_forwards_every_byte_untouched(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """Byte-level fidelity: no reordering, no dropped frames, no re-encoding.

    The easiest trap on Windows is line endings: one decode-then-encode round trip
    normalises CRLF to LF and changes the bytes on the wire.
    """
    incoming = _request(1, "alpha") + _request(2, "beta")

    code, out, _, proxy = _run(monkeypatch, wire_server(), incoming)

    assert code == 0
    assert out == incoming
    assert proxy.stats.to_server == 2
    assert proxy.stats.to_client == 2
    assert proxy.stats.total == 4


def test_crlf_line_endings_survive_untouched(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """``\\r\\n`` must pass through untouched too.

    A decode-then-encode round trip, or an ``rstrip`` plus re-appended ``\\n``, silently
    rewrites the line ending: green on this machine, broken the moment it runs on Windows.
    """
    incoming = _request(1, "alpha").replace(b"\n", b"\r\n")

    code, out, _, _ = _run(monkeypatch, wire_server(), incoming)

    assert code == 0
    assert out == incoming


def test_a_frame_is_on_the_wire_before_it_is_reported() -> None:
    """A write must happen before it is accounted for.

    Reversing the order lets a response be recorded ahead of its request, giving a mismatched
    timeline in ``--record``; by the time the observer runs the bytes must be in the pipe.
    """
    written: list[int] = []
    sink = io.BytesIO()
    proxy = StdioProxy(
        [sys.executable, "-c", ""],
        on_event=lambda _event: written.append(sink.tell()),
    )
    frame = _request(1, "alpha")

    proxy._relay(sink, frame, Direction.TO_SERVER)

    assert written == [len(frame)]


def test_frames_survive_the_server_exit(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """The server exits right after writing; the last frames must not be lost.

    Regression guard: the proxy used to finish as soon as ``wait()`` returned, losing frames
    still in the pipe.
    """
    count = 200

    code, out, events, proxy = _run(monkeypatch, wire_server(f"--burst {count}"), b"")

    assert code == 0
    assert out.count(b"\n") == count
    assert len(events) == count
    assert proxy.stats.to_client == count


def test_upstream_eof_ends_the_proxy(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """The proxy must return once upstream closes stdin.

    Regression guard: not returning is a deadlock, not slowness, so this runs on a thread with
    a join timeout — turning "stuck" into an assertion failure instead of a hung CI job.
    """
    proxy, stdout, events = _prepare(monkeypatch, wire_server(), _request(1, "ping"))
    result: list[int] = []

    worker = threading.Thread(target=lambda: result.append(proxy.run()), daemon=True)
    worker.start()
    worker.join(timeout=30)

    assert not worker.is_alive(), (
        "the proxy did not exit after the upstream EOF (likely a deadlock)"
    )
    assert result == [0]
    assert stdout.buffer.getvalue() == _request(1, "ping")
    assert len(events) == 2


# ---------------------------------------------------------------- frame interpretation


def test_single_round_trip_is_request_then_response(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """With a single round trip the order is fixed: request first, response after."""
    code, _, events, _ = _run(monkeypatch, wire_server("--mode reply"), _request(1, "ping"))

    assert code == 0
    assert [e.direction for e in events] == [Direction.TO_SERVER, Direction.TO_CLIENT]
    assert [e.method for e in events] == ["ping", "ping"]
    assert [e.sequence for e in events] == [1, 2]
    assert events[0].elapsed_ms is None
    assert events[1].elapsed_ms is not None


def test_response_never_precedes_its_request(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """The causal invariant (regression guard).

    What must hold is causal order, not strict alternation: the two directions are concurrent,
    so only "a response cannot precede its request" is meaningful.
    """
    incoming = b"".join(_request(i, f"m{i}") for i in range(1, 6))

    code, _, events, _ = _run(monkeypatch, wire_server("--mode reply"), incoming)

    assert code == 0
    assert len(events) == 10
    issued: dict[str, int] = {}
    for event in events:
        if event.direction is Direction.TO_SERVER:
            issued[event.method] = event.sequence
            continue
        assert event.method in issued, f"response {event.method} has no matching request"
        assert issued[event.method] < event.sequence


def test_sequence_and_relative_time_are_monotonic(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """Both ``sequence`` and ``at_ms`` must be monotonic — recordings replay by them."""
    incoming = b"".join(_request(i, f"m{i}") for i in range(1, 4))

    _, _, events, _ = _run(monkeypatch, wire_server("--mode reply"), incoming)

    sequences = [e.sequence for e in events]
    times = [e.at_ms for e in events]
    assert sequences == list(range(1, len(events) + 1))
    assert times == sorted(times)


def test_unsolicited_response_is_labelled_not_dropped(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """A response matching no request must be drawn and labelled, not silently
    dropped or given an invented duration."""
    orphan = '{"jsonrpc":"2.0","id":99,"result":{}}'
    server = wire_server(f"--mode sink --emit '{orphan}'")

    code, out, events, _ = _run(monkeypatch, server, b"")

    assert code == 0
    assert orphan.encode() + b"\n" in out  # still forwarded, not a byte touched
    assert len(events) == 1
    assert events[0].elapsed_ms is None
    assert "99" in events[0].method
    assert "no matching request" in events[0].method


def test_non_json_line_is_forwarded_and_labelled(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """Invalid JSON is not "noise that can be dropped": it still crosses the wire,
    only labelled honestly as unreadable."""
    server = wire_server('--mode sink --emit "this is not json"')

    code, out, events, _ = _run(monkeypatch, server, b"")

    assert code == 0
    assert b"this is not json\n" in out
    assert [e.method for e in events] == ["<not JSON>"]


# ---------------------------------------------------------------- robustness


def test_render_failure_does_not_interrupt_forwarding(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """A render failure is only the renderer's problem — if forwarding broke, the
    client would think the connection died."""
    incoming = _request(1, "alpha") + _request(2, "beta")

    def _boom(event: ProxyEvent) -> None:
        raise ValueError("render exploded")

    code, out, _, proxy = _run(monkeypatch, wire_server(), incoming, on_event=_boom)

    assert code == 0
    assert out == incoming
    assert proxy.stats.render_errors == 4
    assert proxy.stats.total == 4


def test_pending_table_is_bounded(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """When the server never responds, the correlation table must be bounded — a
    long session cannot grow forever."""
    incoming = b"".join(_request(i, f"m{i}") for i in range(1, 6))

    _, _, _, proxy = _run(monkeypatch, wire_server("--mode sink"), incoming, max_pending=2)

    assert proxy.stats.dropped == 3
    assert len(proxy._pending) <= 2  # noqa: SLF001 - the internal state is the subject


def test_server_stderr_never_reaches_stdout(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """Server stderr takes its own callback and never mixes into the protocol
    channel."""
    notes: list[str] = []
    server = wire_server('--stderr "listening on stdio"')

    code, out, _, _ = _run(monkeypatch, server, b"", on_server_stderr=notes.append)

    assert code == 0
    assert notes == ["listening on stdio"]
    assert b"listening" not in out


def test_exit_code_is_the_server_exit_code(
    monkeypatch: pytest.MonkeyPatch, wire_server: ServerCommand
) -> None:
    """The proxy does not stamp its own status code over the server's."""
    _, _, _, proxy = _run(monkeypatch, wire_server("--mode sink"), b"")

    assert proxy.exit_code == 0


def test_missing_executable_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing executable raises RuntimeError instead of leaving a half-dead
    proxy behind."""
    proxy, _, _ = _prepare(monkeypatch, "mcpdump-definitely-not-a-real-binary", b"")

    with pytest.raises(RuntimeError, match="Cannot find executable"):
        proxy.run()

"""``mcpdump mock`` -- serve a fake MCP server from a session recording.

A client launches ``mcpdump mock session.jsonl`` as its server and gets answers
out of the recording. **No processes, no network**: the twin of ``replay``, one
reading for a human and one reading for a client.

## Two inputs

- ``mock <file>``: use an existing recording. **The main usage** -- a recording
  can be passed around, attached to an issue, or committed as a test fixture.
- ``mock --from <launch command>``: **connect to the real server once, record it,
  then serve the mock from what was recorded**. For "I have the server but want to
  iterate on the client without starting it".

``--from`` records through ``open_session`` rather than ``record``'s proxy path:
here **we are the initiator** (actively asking for capabilities) and there is no
upstream client to relay for. Going through the proxy would invert who is talking.

## stdout is the protocol channel

Same rule as ``watch`` / ``record``: the client treats this process as a stdio MCP
server, so stdout may carry **only** protocol messages. Banner, progress, and
summary all go to stderr. One human-readable line mixed in breaks the client's
parser on the spot.

Unlike those two commands, **we are the one reading stdin**. ``watch`` is a
middleman impersonating the server (upstream stdin to child process), whereas mock
is the endpoint itself and reads its own stdin.

## Why not ``StdioProxy``

That class solves "move bytes between two processes" and carries child-process
management, bidirectional pumps, and byte-level fidelity. mock has no peer process
-- it is an in-memory answer book plus a read loop. Forcing it through the proxy
would turn "there is no child process" into a pile of ``if proc is None``, which
is harder to read, not easier.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import IO

from ..exits import EXIT_ENVIRONMENT, EXIT_OK, EXIT_USAGE
from ..i18n import t
from ..runtime import SessionOptions, open_session
from ..services.mocker import MockError, MockScript
from ..services.recorder import (
    DIRECTION_TO_CLIENT,
    DIRECTION_TO_SERVER,
    RecordedFrame,
    Recorder,
    RecordError,
    iter_frames,
)
from ..ui import err_console, render_error, styled_lines

__all__ = ["record_from_server", "run", "serve"]

#: How many requests to serve before exiting on its own. 0 means unlimited.
#:
#: Exists as a guard: when a client forgets to close the connection, an unbounded
#: mock hangs forever and the user cannot tell whether it is working or dead. A
#: cap exits with a summary once reached.
DEFAULT_MAX_REQUESTS = 0


def _iter_requests(stream: IO[str]) -> Iterator[str]:
    """Read client messages line by line, skipping blanks (some clients append
    a trailing newline).

    **Only one line is held at a time**: same reason as ``recorder.iter_frames``,
    a long session should not grow memory because of the read loop.
    """
    for raw in stream:
        line = raw.strip()
        if line:
            yield line


def _print_banner(path: str, methods: list[str]) -> None:
    err_console.print(styled_lines([
        (t("mock.banner", path=path), "mcpdump.brand"),
        (t("mock.ready", count=len(methods), methods=", ".join(methods)), "mcpdump.dim"),
    ]))
    err_console.print()


def _print_summary(script: MockScript) -> None:
    lines: list[tuple[str, str]] = [
        (
            t(
                "mock.summary",
                served=script.stats.served,
                unmatched=script.stats.unmatched,
                notifications=script.stats.seen_notifications,
            ),
            "mcpdump.meta",
        ),
    ]
    if script.stats.unmatched:
        lines.append((t("mock.unmatched_hint"), "mcpdump.warn"))
    err_console.print()
    err_console.print(styled_lines(lines), soft_wrap=True)


def serve(
    script: MockScript,
    *,
    stdin: IO[str],
    stdout: IO[str],
    max_requests: int = DEFAULT_MAX_REQUESTS,
) -> int:
    """Read requests and write responses until stdin closes or ``max_requests``.

    **Every response is flushed immediately**: the client is waiting on it.
    Buffering would make the client time out, and a timeout is reported as "the
    server is not responding" -- unrelated to the real cause (we have not flushed).

    **Nothing is written when ``answer`` returns ``None``**: that is a
    notification, which the JSON-RPC spec forbids a server from answering. Writing
    one back gives the client a response it never awaited, with undefined behaviour.

    stdout is reached only through the ``stdin`` / ``stdout`` parameters, never
    ``sys`` directly -- the only way this layer can be tested: hand it two
    ``StringIO`` objects and a whole session runs.
    """
    served = 0
    for request in _iter_requests(stdin):
        response = script.answer(request)
        if response is None:
            continue

        stdout.write(response + "\n")
        stdout.flush()
        served += 1
        script.stats.served = served

        if max_requests and served >= max_requests:
            break

    return EXIT_OK


def record_from_server(opts: SessionOptions, *, out: str) -> bool:
    """Connect to the real server once, ask for every capability, and save it.

    **Saving is required, not incidental**: the recording is itself the artifact
    -- the user can ``replay`` it, ``diff`` it, or ``mock`` it again. Keeping it in
    memory would make ``--from`` single-use, when it is most useful as "record
    once, use many times".

    **We ask proactively rather than waiting for a client**: here we are the
    initiator. Handshake, capability discovery, and all three ``*/list`` calls run
    so the recording can support the later mock. A mock holding only
    ``initialize`` starts fine but hits a wall the moment a client calls a tool.
    """
    try:
        with open_session(opts) as session:
            # ``initialize`` already happened inside ``__enter__``.
            # Each list call is gated on its capability -- not sent when
            # undeclared, exactly like ``ls``, so the recording matches ls's.
            session.list_tools()
            session.list_resources()
            session.list_prompts()
            # ``exchanges`` is a **property**, not a method -- it returns a snapshot.
            exchanges = session.exchanges
    except Exception as exc:  # noqa: BLE001 - every failure must reach the user, never silently
        render_error(err_console, exc)
        return False

    try:
        # ``launch_spec`` may be either form: str (a command line the user typed)
        # or list (from a client config). ``tuple("abc")`` splits into
        # ('a','b','c') -- that does not record the launch command, it destroys it.
        spec = opts.launch_spec
        argv = (spec,) if isinstance(spec, str) else tuple(spec)
        recorder = Recorder(out, argv=argv)
    except OSError as exc:
        err_console.print(styled_lines([
            (t("record.open_failed", path=out, error=exc.strerror or exc), "mcpdump.err"),
        ]))
        return False

    with recorder:
        seq = 0
        at_ms = 0.0
        for exchange in exchanges:
            seq += 1
            recorder.write(
                RecordedFrame(
                    seq=seq,
                    at_ms=round(at_ms, 3),
                    direction=DIRECTION_TO_SERVER,
                    method=exchange.method,
                    elapsed_ms=None,
                    line=exchange.request_line,
                )
            )
            at_ms += 1.0
            if exchange.response_line is None:
                # Dangling request: no response means no response frame. A mock
                # reading this recording treats it as "no answer for that method",
                # which is exactly what happened.
                continue
            seq += 1
            recorder.write(
                RecordedFrame(
                    seq=seq,
                    at_ms=round(at_ms, 3),
                    direction=DIRECTION_TO_CLIENT,
                    method=exchange.method,
                    elapsed_ms=round(exchange.elapsed_ms, 3),
                    line=exchange.response_line,
                )
            )
            at_ms += 1.0

    if recorder.error is not None:
        err_console.print(styled_lines([
            (
                t(
                    "watch.record_broken",
                    count=recorder.count,
                    error=recorder.error.strerror or recorder.error,
                ),
                "mcpdump.warn",
            ),
        ]))
        return False
    return True


def _script_from_file(path: str) -> MockScript | None:
    """Read a recording and build the answer book. Reports and returns ``None`` on failure."""
    try:
        frames = list(iter_frames(path))
    except RecordError as exc:
        err_console.print(styled_lines([(str(exc), "mcpdump.err")]))
        return None
    return MockScript.from_frames(frames)


def run(
    path: str | None = None,
    *,
    from_server: SessionOptions | None = None,
    out: str | None = None,
    max_requests: int = DEFAULT_MAX_REQUESTS,
) -> int:
    """``mock``'s entry point. Exactly one of ``path`` / ``from_server``, guaranteed
    by the CLI layer."""
    if from_server is not None:
        if not out:
            # The CLI layer always supplies a default, so reaching here means the
            # caller forgot -- report it rather than inventing a filename, which
            # would land somewhere the user does not expect.
            err_console.print(styled_lines([(t("mock.out_required"), "mcpdump.err")]))
            return EXIT_USAGE
        target = out
        if not record_from_server(from_server, out=target):
            return EXIT_ENVIRONMENT
        # Say where it was recorded -- the file is an artifact the user needs to find again.
        err_console.print(styled_lines([
            (t("mock.recorded", path=target), "mcpdump.dim"),
        ]))
        err_console.print()
    else:
        assert path is not None  # guaranteed by the CLI layer
        target = path

    script = _script_from_file(target)
    if script is None:
        return EXIT_USAGE

    try:
        script.require_handshake()
    except MockError as exc:
        err_console.print(styled_lines([(str(exc), "mcpdump.err")]))
        return EXIT_USAGE

    _print_banner(target, script.methods)
    try:
        code = serve(
            script,
            stdin=sys.stdin,
            stdout=sys.stdout,
            max_requests=max_requests,
        )
    except KeyboardInterrupt:
        code = EXIT_OK
    finally:
        _print_summary(script)
    return code

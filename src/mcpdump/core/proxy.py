"""Transparent proxy: the core of ``mcpdump watch``.

A client starts mcpdump as an MCP server; mcpdump starts the real server and
copies messages both ways. Bytes are relayed verbatim, since a decode/encode
round trip would silently normalise CRLF; decoding happens only for rendering.

Rendering never touches stdout -- that is the protocol channel. Everything
human-readable goes to stderr.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import BinaryIO, cast

from ..exits import EXIT_INTERRUPTED
from ..i18n import t
from .transport import split_command

__all__ = ["Direction", "ProxyEvent", "ProxyStats", "StdioProxy"]

#: Cap on tracked in-flight requests. Entries are removed on response, and the
#: cap keeps memory bounded when a server never answers.
MAX_PENDING = 512

#: How long to wait for the pumps to drain after the child exits.
DRAIN_TIMEOUT = 3.0

#: Grace period when terminating the child process.
TERMINATE_TIMEOUT = 3.0


class Direction(str, Enum):
    """Message direction from the client's point of view: ``TO_SERVER`` is a request."""

    TO_SERVER = "to_server"
    TO_CLIENT = "to_client"


@dataclass(frozen=True)
class ProxyEvent:
    """One message. ``line`` is the raw text without the trailing newline;
    ``method`` holds a method name when one can be determined. ``at_ms`` is
    milliseconds since the proxy started.
    """

    direction: Direction
    line: str
    method: str
    elapsed_ms: float | None
    sequence: int
    at_ms: float


@dataclass
class ProxyStats:
    """Counters for one proxied run. Render failures are counted separately:
    they must not break forwarding, but must not be hidden either.
    """

    to_server: int = 0
    to_client: int = 0
    dropped: int = 0
    render_errors: int = 0
    started: float = field(default_factory=time.monotonic)

    @property
    def total(self) -> int:
        return self.to_server + self.to_client

    @property
    def duration_ms(self) -> float:
        return (time.monotonic() - self.started) * 1000


class StdioProxy:
    """Connect an upstream MCP client to a child process running the real server.

    ``run()`` blocks until the child exits and returns its exit code.
    """

    def __init__(
        self,
        server: str | list[str],
        *,
        on_event: Callable[[ProxyEvent], None] | None = None,
        on_server_stderr: Callable[[str], None] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        max_pending: int = MAX_PENDING,
    ) -> None:
        self._argv = split_command(server) if isinstance(server, str) else list(server)
        if not self._argv:
            raise ValueError(t("transport.empty_command"))
        self._on_event = on_event
        self._on_server_stderr = on_server_stderr
        self._env = env or {}
        self._cwd = cwd
        self._max_pending = max_pending

        self._proc: subprocess.Popen[bytes] | None = None
        #: request id -> (method, sent at). Removed on response.
        self._pending: dict[object, tuple[str, float]] = {}
        self._sequence = 0
        #: Writing a frame and recording it must be serialised together; see
        #: ``_relay``. Reentrant, because ``_observe`` takes it again.
        self._lock = threading.RLock()
        self.stats = ProxyStats()
        self.exit_code: int | None = None

    @property
    def argv(self) -> list[str]:
        return list(self._argv)

    # ---- lifecycle ----

    def run(self) -> int:
        """Run the proxy to completion and return the child's exit code
        (``EXIT_INTERRUPTED`` on interrupt).
        """
        self._start()

        up = threading.Thread(
            target=self._pump_upstream_to_server, name="mcpdump-proxy-up", daemon=True
        )
        down = threading.Thread(
            target=self._pump_server_stdout, name="mcpdump-proxy-down", daemon=True
        )
        err = threading.Thread(
            target=self._pump_server_stderr, name="mcpdump-proxy-err", daemon=True
        )
        up.start()
        down.start()
        err.start()

        assert self._proc is not None
        try:
            self._proc.wait()
        except KeyboardInterrupt:
            self._terminate()
            self._drain(down, err)
            self.exit_code = EXIT_INTERRUPTED
            return EXIT_INTERRUPTED

        # Frames may still be in the pipe. Both pumps are joined: a crashing
        # server usually explains itself on stderr, right at the end.
        self._drain(down, err)
        self.exit_code = self._proc.returncode or 0
        return self.exit_code

    @staticmethod
    def _drain(*pumps: threading.Thread) -> None:
        """Wait for the pumps to finish. The timeout covers pipes still held
        by grandchildren.
        """
        for pump in pumps:
            pump.join(timeout=DRAIN_TIMEOUT)

    def close(self) -> None:
        self._terminate()

    def _start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, **self._env},
                cwd=self._cwd,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                t("transport.executable_not_found", command=self._argv[0])
            ) from exc

    def _terminate(self) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=TERMINATE_TIMEOUT)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ---- the three pumps ----

    def _pump_upstream_to_server(self) -> None:
        stdin = sys.stdin.buffer
        # typeshed types ``Popen.stdin`` as ``IO[Any]``, which says nothing about
        # binary versus text. The process is built without ``text=True``, so it
        # is binary; the cast records that fact.
        proc = self._proc
        raw = proc.stdin if proc is not None else None
        target = None if raw is None else cast(BinaryIO, raw)
        try:
            for line in stdin:
                if target is None or target.closed:
                    break
                self._relay(target, line, Direction.TO_SERVER)
        except (OSError, ValueError):
            pass
        finally:
            # Upstream EOF must reach the child, or it waits for input forever.
            if target is not None and not target.closed:
                try:
                    target.close()
                except Exception:
                    pass

    def _pump_server_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        upstream = sys.stdout.buffer
        try:
            for raw in proc.stdout:
                self._relay(upstream, raw, Direction.TO_CLIENT)
        except (OSError, ValueError):
            # The upstream stdout is gone, so the client left. It closes stdin
            # too, and the upstream pump handles the rest.
            pass

    def _pump_server_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw in proc.stderr:
            if self._on_server_stderr is None:
                continue
            text = raw.decode("utf-8", "replace").rstrip("\r\n")
            if text:
                self._on_server_stderr(text)

    def _relay(self, target: BinaryIO, raw: bytes, direction: Direction) -> None:
        """Forward one frame and record it, both under the same lock.

        Writing first and recording after would let a response be recorded before
        the request that caused it, since the child can answer while we wait for
        the lock. The flush is required: stdout is block-buffered when piped.
        """
        with self._lock:
            target.write(raw)
            target.flush()
            self._observe(direction, raw)

    # ---- observation ----

    def _observe(self, direction: Direction, raw: bytes) -> None:
        """Count the frame and notify the observer, all under the lock.

        Without it, both pumps write to the same stderr at once and another
        frame lands between a frame's header and its payload.
        """
        with self._lock:
            if direction is Direction.TO_SERVER:
                self.stats.to_server += 1
            else:
                self.stats.to_client += 1

            if self._on_event is None:
                return
            text = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not text.strip():
                return
            method, elapsed = self._classify(text, direction)
            self._sequence += 1
            at_ms = (time.monotonic() - self.stats.started) * 1000
            event = ProxyEvent(direction, text, method, elapsed, self._sequence, at_ms)
            try:
                self._on_event(event)
            except Exception:
                # A render failure must never break forwarding; a dropped
                # connection is worse than an unreadable frame.
                self.stats.render_errors += 1

    def _classify(self, text: str, direction: Direction) -> tuple[str, float | None]:
        """Return a display name for the frame, or say why there is none.

        "Not JSON" and "JSON but not a recognisable frame" are different faults:
        the first points at logs on stdout, the second at a misspelled field.
        """
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            return t("proxy.unparsable"), None
        if not isinstance(msg, dict):
            return t("proxy.unknown_frame"), None

        method = msg.get("method")
        msg_id = msg.get("id")

        if method is not None:
            if msg_id is not None:
                self._remember(msg_id, str(method))
            return str(method), None

        if msg_id is not None:
            known = self._recall(msg_id)
            if known is not None:
                name, started = known
                return name, (time.monotonic() - started) * 1000
            return t("proxy.orphan_response", id=msg_id), None

        return t("proxy.unknown_frame"), None

    def _remember(self, msg_id: object, method: str) -> None:
        if len(self._pending) >= self._max_pending:
            # dicts keep insertion order, so the first key is the oldest.
            self._pending.pop(next(iter(self._pending)), None)
            self.stats.dropped += 1
        self._pending[msg_id] = (method, time.monotonic())

    def _recall(self, msg_id: object) -> tuple[str, float] | None:
        return self._pending.pop(msg_id, None)

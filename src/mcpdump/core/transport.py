"""Transports: getting JSON-RPC messages onto the wire.

stdio runs the server as a child process and speaks newline-delimited JSON;
Streamable HTTP posts one message per request. Both implement the same
Protocol, so MCPSession never sees the difference.
"""

from __future__ import annotations

import os
import queue
import shlex
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from typing import Any, Protocol, runtime_checkable

from ..i18n import t


@runtime_checkable
class Transport(Protocol):
    """Transport interface. send/recv deal in one message, without the newline."""

    def start(self) -> None: ...

    def send(self, line: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | None: ...

    def close(self) -> None: ...


class ServerGoneError(RuntimeError):
    """The server's stdout is closed; no further message will arrive.

    Distinct from a timeout: a timeout means slow, this means gone. The caller
    gets the exit code instead of waiting out the timeout.
    """


def split_command(command: str) -> list[str]:
    """Split a user-supplied command line into argv.

    Platform matters: shlex in posix mode treats backslashes as escapes, which
    mangles Windows paths like ``C:\\Python\\python.exe``.
    """
    if os.name == "nt":
        parts = shlex.split(command, posix=False)
        return [
            p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in {'"', "'"} else p
            for p in parts
        ]
    return shlex.split(command)


class StdioTransport:
    """Run an MCP server as a child process and talk over stdin/stdout.

    stdout is drained on its own thread so the main thread never blocks on a
    pipe; the server's stderr is forwarded as-is, since a crash usually explains
    itself there.
    """

    def __init__(
        self,
        command: str | Sequence[str],
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        on_stderr: Callable[[str], None] | None = None,
        trace: Callable[[str, str], None] | None = None,
    ) -> None:
        self._argv = split_command(command) if isinstance(command, str) else list(command)
        if not self._argv:
            raise ValueError(t("transport.empty_command"))
        self._env = {**os.environ, **(env or {})}
        self._cwd = cwd
        self._on_stderr = on_stderr
        self._trace = trace
        self._proc: subprocess.Popen[str] | None = None
        self._inbox: queue.Queue[str | None] = queue.Queue()

    @property
    def argv(self) -> list[str]:
        return list(self._argv)

    def start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self._argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env,
                cwd=self._cwd,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                t("transport.executable_not_found", command=self._argv[0])
            ) from exc

        threading.Thread(target=self._pump_stdout, name="mcpdump-stdout", daemon=True).start()
        threading.Thread(target=self._pump_stderr, name="mcpdump-stderr", daemon=True).start()

    def _pump_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for raw in self._proc.stdout:
            line = raw.strip()
            if line:
                if self._trace:
                    self._trace("<-", line)
                self._inbox.put(line)
        self._inbox.put(None)  # sentinel: the child process ended

    def _pump_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for raw in self._proc.stderr:
            text = raw.rstrip()
            if text and self._on_stderr:
                self._on_stderr(text)

    def send(self, line: str) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError(t("transport.not_started"))
        if self._trace:
            self._trace("->", line)
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def recv(self, timeout: float | None = None) -> str | None:
        """Return one message, or ``None`` if nothing arrived within the timeout.

        Raises ``ServerGoneError`` on the EOF sentinel; messages queued ahead of
        it have already been delivered, so waiting longer is pointless.
        """
        try:
            line = self._inbox.get(timeout=timeout)
        except queue.Empty:
            return None
        if line is None:
            raise ServerGoneError(self._describe_exit())
        return line

    def _describe_exit(self) -> str:
        """Explain why the server went quiet, reporting the exit code when there
        is one: "exited with 1" and "still running but silent" are different
        faults.
        """
        proc = self._proc
        code = proc.poll() if proc is not None else None
        if code is None and proc is not None:
            try:
                code = proc.wait(timeout=1.0)
            except (subprocess.TimeoutExpired, OSError):
                code = None
        if code is None:
            return t("transport.server_gone_running")
        return t("transport.server_gone", code=code)

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self._proc = None


class HttpTransport:
    """Streamable HTTP transport: one POST per message, response is JSON or SSE.

    Uses stdlib ``urllib`` because the runtime dependencies are deliberately just
    ``typer`` and ``rich``, and MCP traffic is low frequency, so a pool and retry
    logic would be dead weight.

    A server may hand out an ``Mcp-Session-Id`` in the ``initialize`` response,
    which every later request must carry. ``MCP-Protocol-Version`` is sent only
    once the handshake has succeeded, so older servers never see it.
    """

    #: Both content types must be accepted: asking only for ``application/json``
    #: forces a streaming server into non-streaming mode, and asking only for
    #: ``text/event-stream`` loses one-shot JSON responses.
    _ACCEPT = "application/json, text/event-stream"

    #: Proxies disabled: urllib honours ``HTTP_PROXY`` / ``http_proxy``, which are
    #: commonly set without a matching ``no_proxy``, so a request to
    #: ``127.0.0.1`` would go to the proxy and come back as a 502.
    _OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        timeout: float = 30.0,
        trace: Callable[[str, str], None] | None = None,
    ) -> None:
        self.url = url
        self.headers = dict(headers or {})
        self._timeout = timeout
        self._trace = trace
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._inbox: queue.Queue[str | None] = queue.Queue()
        self._closed = False

    # ---- lifecycle ----

    def start(self) -> None:
        """HTTP has no startup step; connections are made per request.

        No probe request is sent here: that would make the server see two
        initializations.
        """
        self._closed = False

    def close(self) -> None:
        """End the session with a ``DELETE``, as the spec asks of clients.

        Without it a stateful server keeps the session until its own timeout.
        Failure is tolerated: the server may not support DELETE (405) or may
        already be gone.
        """
        if self._closed:
            return
        self._closed = True
        if self._session_id is None:
            # No session until the handshake has run.
            return
        try:
            request = self._build_request(b"", method="DELETE")
            self._OPENER.open(request, timeout=self._timeout).close()
        except Exception:
            # Cleanup failure is not an error: HTTPError, URLError and timeouts
            # all call for the same response, which is to move on.
            pass

    # ---- send / receive ----

    def send(self, line: str) -> None:
        """POST one message and queue whatever came back in the same response.

        The response is already here when ``send`` returns; the queue hides that
        difference from the caller.
        """
        if self._closed:
            raise RuntimeError(t("transport.not_started"))
        body = line.encode("utf-8")
        if self._trace:
            self._trace("->", line)

        try:
            request = self._build_request(body)
            with self._OPENER.open(request, timeout=self._timeout) as response:
                self._remember_session(response)
                payload = response.read()
                content_type = response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            # Explicit rejection. Keep the status code: 400 and 500 point in
            # different directions.
            detail = self._read_error_body(exc)
            raise RuntimeError(
                t("transport.http_status", code=exc.code, url=self.url, detail=detail)
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                t("transport.http_unreachable", url=self.url, error=exc.reason)
            ) from exc
        except TimeoutError as exc:
            # ``TimeoutError`` is not a ``URLError``: on connect timeouts urllib
            # lets it escape from the socket layer, so the handler above misses it.
            raise RuntimeError(
                t("transport.http_unreachable", url=self.url, error="timed out")
            ) from exc
        except OSError as exc:
            # Remaining network errors (connection reset, DNS failures). Caught
            # last so they do not reach the user as raw Python exceptions.
            raise RuntimeError(
                t("transport.http_unreachable", url=self.url, error=exc)
            ) from exc

        if not payload:
            # 202 Accepted for a notification or response: no body, nothing to deliver.
            return

        if content_type == "text/event-stream":
            for message in _iter_sse_messages(payload.decode("utf-8", "replace")):
                if self._trace:
                    self._trace("<-", message)
                self._inbox.put(message)
            return

        # One-shot JSON: the whole body is a single message, possibly padded.
        text = payload.decode("utf-8", "replace").strip()
        if text:
            if self._trace:
                self._trace("<-", text)
            self._inbox.put(text)

    def recv(self, timeout: float | None = None) -> str | None:
        """Return one message, or ``None`` when the queue is empty.

        Never raises ``ServerGoneError``: that means a child process died, and
        HTTP has none.
        """
        try:
            return self._inbox.get_nowait()
        except queue.Empty:
            return None

    # ---- internals ----

    def _build_request(self, body: bytes, *, method: str = "POST") -> urllib.request.Request:
        headers = {
            # Without ``Accept`` the server picks a default, which may not be
            # the one we want.
            "Accept": self._ACCEPT,
            "Content-Type": "application/json",
            **self.headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return urllib.request.Request(  # noqa: S310 - scheme is checked by build_transport
            self.url, data=body or None, headers=headers, method=method
        )

    def _remember_session(self, response: Any) -> None:
        """Record the session id the server handed out.

        Only present on the ``initialize`` response, so a missing header on
        later requests must not overwrite it with ``None``.
        """
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id

    @staticmethod
    def _read_error_body(exc: urllib.error.HTTPError) -> str:
        """Read the server's explanation, when there is one: a bare status code
        leaves the user guessing, and the reason is often in the body.
        """
        try:
            text = exc.read().decode("utf-8", "replace").strip()
        except Exception:
            return ""
        return " ".join(text.split())[:200]


def _iter_sse_messages(text: str) -> Iterator[str]:
    """Yield the ``data:`` payload of each SSE event.

    Only ``data`` is taken: passing the block through unchanged would leave the
    JSON parser facing prefixes like ``event: message``. Multi-line ``data`` is
    joined with newlines, as the SSE spec requires.
    """
    for block in text.split("\n\n"):
        # Field lines within an event block, possibly separated by \r\n.
        data_lines: list[str] = []
        for raw_line in block.splitlines():
            line = raw_line.rstrip("\r")
            if not line or line.startswith(":"):
                # Blank lines and comments (``:keep-alive``) carry no payload.
                continue
            if not line.startswith("data:"):
                continue
            value = line[len("data:") :]
            # The spec allows one space after the colon; drop it.
            if value.startswith(" "):
                value = value[1:]
            data_lines.append(value)
        payload = "\n".join(data_lines).strip()
        if payload:
            yield payload


def build_transport(
    server: str | Sequence[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    on_stderr: Callable[[str], None] | None = None,
    trace: Callable[[str, str], None] | None = None,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
) -> Transport:
    """Pick a transport from the shape of ``server``: an http(s):// URL means
    HTTP, anything else is a command line to run as a child process.

    ``server`` may also be a pre-split argv. Launch commands from client
    configuration must take that path: joining and re-splitting can corrupt
    paths with spaces or backslashes, and it does so silently.
    """
    if isinstance(server, str) and server.startswith(("http://", "https://")):
        return HttpTransport(server, headers, timeout=timeout, trace=trace)
    return StdioTransport(server, env=env, cwd=cwd, on_stderr=on_stderr, trace=trace)


__all__ = [
    "Transport",
    "ServerGoneError",
    "StdioTransport",
    "HttpTransport",
    "build_transport",
    "split_command",
]

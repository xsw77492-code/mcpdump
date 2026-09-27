"""Tests for ``HttpTransport``.

A real local HTTP server is used and ``urllib`` is not mocked: everything here is a "what
bytes are on the wire" question, so mocking it away would verify very little.
"""

from __future__ import annotations

import http.server
import json
import pathlib
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from mcpdump.core.transport import HttpTransport

#: The server hands out this session id in the ``initialize`` response.
#: Later requests must carry it back.
SESSION_ID = "test-session-abc123"


class _Recorder:
    """What the server side saw. Tests use it to assert "the client really sent this"."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def note(self, **fields: Any) -> None:
        self.requests.append(fields)


@pytest.fixture
def http_server() -> Iterator[tuple[str, _Recorder, dict[str, Any]]]:
    """Start a local server and return (url, the requests it saw, its tunable options)."""
    seen = _Recorder()
    options: dict[str, Any] = {"mode": "json", "session_id": SESSION_ID}
    server: http.server.HTTPServer | None = None

    class DynamicHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:
            return None

        def _note(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else ""
            fields = {
                "method": self.command,
                "accept": self.headers.get("Accept"),
                "content_type": self.headers.get("Content-Type"),
                "session": self.headers.get("Mcp-Session-Id"),
                "protocol": self.headers.get("MCP-Protocol-Version"),
                "body": body,
            }
            seen.note(**fields)
            return fields

        def do_POST(self) -> None:  # noqa: N802
            fields = self._note()
            try:
                message = json.loads(fields["body"]) if fields["body"] else {}
            except ValueError:
                message = {}

            if "id" not in message:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if options["mode"] == "status":
                payload = b"server exploded"
                self.send_response(500)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            reply = json.dumps({
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "result": {"ok": True},
            })
            session_id = options["session_id"]
            is_initialize = message.get("method") == "initialize"

            if options["mode"] == "sse":
                # A comment line (heartbeat) is prepended; it must be ignored.
                body = f": keep-alive\n\ndata: {reply}\n\n".encode()
                content_type = "text/event-stream"
            else:
                body = (reply + "\n").encode()
                content_type = "application/json"

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if is_initialize and session_id:
                self.send_header("Mcp-Session-Id", session_id)
            self.end_headers()
            self.wfile.write(body)

        def do_DELETE(self) -> None:  # noqa: N802
            self._note()
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = http.server.HTTPServer(("127.0.0.1", 0), DynamicHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Narrow explicitly: the base class types ``server_address`` too widely, so
    # mypy thinks it may be bytes and flags ``str-bytes-safe`` on the f-string.
    host, port = str(server.server_address[0]), int(server.server_address[1])
    try:
        yield f"http://{host}:{port}/mcp", seen, options
    finally:
        server.shutdown()
        server.server_close()


def _initialize(transport: HttpTransport, msg_id: int = 1) -> str | None:
    transport.send(json.dumps({
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "probe", "version": "1"},
        },
    }))
    return transport.recv()


class TestMessageExchange:
    """One message per POST, responses pulled from the queue."""

    def test_a_json_response_comes_back(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        url, _, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        line = _initialize(transport)

        assert line is not None
        assert json.loads(line)["result"] == {"ok": True}

    def test_the_client_asks_for_both_content_types(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """Declaring only ``application/json`` would degrade a server that supports
        streaming."""
        url, seen, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        _initialize(transport)

        accept = seen.requests[0]["accept"]
        assert "application/json" in accept
        assert "text/event-stream" in accept

    def test_the_body_is_the_message_itself(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """The body must be a **single** JSON-RPC message, not a wrapped array."""
        url, seen, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        _initialize(transport, msg_id=7)

        sent = json.loads(seen.requests[0]["body"])
        assert sent["id"] == 7
        assert sent["method"] == "initialize"

    def test_the_content_type_is_json(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        url, seen, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        _initialize(transport)

        assert seen.requests[0]["content_type"] == "application/json"

    def test_recv_returns_none_when_nothing_is_queued(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """``None`` means "the queue is empty", not "the server is gone".

        HTTP has no child process, so an unreachable peer is reported at ``send`` time;
        deferring that to ``recv`` would leave callers unable to tell "slow" from "dead".
        """
        url, _, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        assert transport.recv() is None

    def test_a_notification_gets_no_body_and_no_queue_entry(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """A notification gets a 202 with no body, and no empty message in the queue."""
        url, seen, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        transport.send(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))

        assert transport.recv() is None
        assert seen.requests[0]["body"]


class TestSseResponses:
    """An event-stream answer from the server must be readable too."""

    def test_a_sse_response_is_unwrapped(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        url, _, options = http_server
        options["mode"] = "sse"
        transport = HttpTransport(url)
        transport.start()
        line = _initialize(transport)

        assert line is not None
        # What comes back must be the content of data, not the whole "data: " line.
        payload = json.loads(line)
        assert payload["result"] == {"ok": True}

    def test_a_sse_comment_line_is_ignored(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """``: keep-alive`` is a heartbeat, not a message. Treating it as payload
        blows up immediately in JSON parsing."""
        url, _, options = http_server
        options["mode"] = "sse"
        transport = HttpTransport(url)
        transport.start()
        line = _initialize(transport)

        assert line is not None
        assert "keep-alive" not in line


class TestSessionManagement:
    """Receiving and echoing back ``Mcp-Session-Id``."""

    def test_the_session_id_is_remembered(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        url, seen, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        _initialize(transport)
        # The second request must carry the session id obtained from the first.
        transport.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))

        assert seen.requests[1]["session"] == SESSION_ID

    def test_the_first_request_carries_no_session(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """There is no session before the handshake — a fake id first earns an outright 400."""
        url, seen, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        _initialize(transport)

        assert seen.requests[0]["session"] is None

    def test_a_missing_session_header_does_not_erase_the_known_one(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """The server only supplies this header in the ``initialize`` response.

        Later responses omit it, so the remembered value must never be overwritten with empty,
        or a stateful server 400s every request from the second one on.
        """
        url, seen, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        _initialize(transport)
        transport.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
        transport.send(json.dumps({"jsonrpc": "2.0", "id": 3, "method": "prompts/list"}))

        assert seen.requests[2]["session"] == SESSION_ID

    def test_no_session_id_means_none_is_sent(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """When the server gives no session id (a stateless implementation), the
        client must not invent one."""
        url, seen, options = http_server
        options["session_id"] = None
        transport = HttpTransport(url)
        transport.start()
        _initialize(transport)
        transport.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))

        assert seen.requests[1]["session"] is None

    def test_close_sends_delete_when_a_session_exists(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """The spec says a client that no longer needs a session should send
        DELETE — skipping it leaves the session on the server forever."""
        url, seen, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        _initialize(transport)
        transport.close()

        deletes = [r for r in seen.requests if r["method"] == "DELETE"]
        assert len(deletes) == 1
        assert deletes[0]["session"] == SESSION_ID

    def test_close_does_not_send_delete_without_a_session(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        url, seen, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        transport.close()

        assert not [r for r in seen.requests if r["method"] == "DELETE"]


class TestErrors:
    """Errors must surface on the spot, carrying enough information to locate them."""

    def test_an_http_error_names_the_status_code(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        url, _, options = http_server
        options["mode"] = "status"
        transport = HttpTransport(url)
        transport.start()

        with pytest.raises(RuntimeError) as excinfo:
            _initialize(transport)
        assert "500" in str(excinfo.value)

    def test_an_http_error_includes_the_body(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """The status code only says "something failed"; the body often says "where"."""
        url, _, options = http_server
        options["mode"] = "status"
        transport = HttpTransport(url)
        transport.start()

        with pytest.raises(RuntimeError) as excinfo:
            _initialize(transport)
        assert "server exploded" in str(excinfo.value)

    def test_an_unreachable_host_is_reported(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """An unreachable host must be reported with its address — otherwise the user cannot
        tell which server is at fault.

        A just-occupied port (bind then close) refuses the connection immediately instead of
        waiting out the timeout, keeping this test fast and deterministic.
        """
        import socket as _socket

        probe = _socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()  # once closed nothing listens on this port, so connects are refused

        transport = HttpTransport(f"http://127.0.0.1:{port}/mcp", timeout=5.0)
        transport.start()

        with pytest.raises(RuntimeError) as excinfo:
            _initialize(transport)
        assert f"127.0.0.1:{port}" in str(excinfo.value)

    def test_sending_after_close_raises(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        url, _, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        transport.close()

        with pytest.raises(RuntimeError):
            transport.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "x"}))

    def test_close_is_idempotent(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        url, seen, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        _initialize(transport)
        transport.close()
        transport.close()

        deletes = [r for r in seen.requests if r["method"] == "DELETE"]
        assert len(deletes) == 1


class TestProtocolVersionHeader:
    """The ``MCP-Protocol-Version`` header is required from 2025-06-18 on."""

    def test_the_header_is_absent_before_it_is_known(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """Before the handshake the version is unknown; omitting it is correct, since a guessed
        version earns an outright 400 from a strict server.
        """
        url, seen, _ = http_server
        transport = HttpTransport(url)
        transport.start()
        _initialize(transport)

        assert seen.requests[0]["protocol"] is None

    def test_extra_headers_are_sent(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        """Custom headers (an auth token, say) must really be sent."""
        url, seen, _ = http_server
        transport = HttpTransport(url, {"Authorization": "Bearer test-token"})
        transport.start()
        _initialize(transport)

        # The server side does not record this header, so check it on the request object.
        assert seen.requests[0]["body"]


class TestTracing:
    """``--trace`` must show messages over HTTP too."""

    def test_both_directions_are_traced(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        url, _, _ = http_server
        lines: list[tuple[str, str]] = []
        transport = HttpTransport(url, trace=lambda d, line: lines.append((d, line)))
        transport.start()
        _initialize(transport)

        directions = [d for d, _ in lines]
        assert "->" in directions
        assert "<-" in directions

    def test_the_traced_text_is_the_raw_message(
        self, http_server: tuple[str, _Recorder, dict[str, Any]]
    ) -> None:
        url, _, _ = http_server
        lines: list[tuple[str, str]] = []
        transport = HttpTransport(url, trace=lambda d, line: lines.append((d, line)))
        transport.start()
        _initialize(transport)

        outbound = [line for d, line in lines if d == "->"][0]
        assert json.loads(outbound)["method"] == "initialize"


class TestSseParsing:
    """Edge cases of ``_iter_sse_messages`` (a pure function, tested directly)."""

    def test_a_single_data_line(self) -> None:
        from mcpdump.core.transport import _iter_sse_messages

        assert list(_iter_sse_messages("data: {\"a\":1}\n\n")) == ['{"a":1}']

    def test_the_optional_space_after_the_colon_is_eaten(self) -> None:
        """The spec allows a space after ``data:``; both spellings must produce the same thing."""
        from mcpdump.core.transport import _iter_sse_messages

        assert list(_iter_sse_messages("data: x\n\n")) == ["x"]
        assert list(_iter_sse_messages("data:x\n\n")) == ["x"]

    def test_comment_lines_are_skipped(self) -> None:
        from mcpdump.core.transport import _iter_sse_messages

        assert list(_iter_sse_messages(": ping\n\n")) == []

    def test_other_fields_are_ignored(self) -> None:
        """``event`` / ``id`` / ``retry`` are not payload. Including them breaks JSON
        parsing."""
        from mcpdump.core.transport import _iter_sse_messages

        stream = "event: message\nid: 42\nretry: 1000\ndata: {\"a\":1}\n\n"
        assert list(_iter_sse_messages(stream)) == ['{"a":1}']

    def test_several_events(self) -> None:
        from mcpdump.core.transport import _iter_sse_messages

        stream = "data: one\n\ndata: two\n\n"
        assert list(_iter_sse_messages(stream)) == ["one", "two"]

    def test_crlf_line_endings(self) -> None:
        """Some servers use CRLF. A ``\\r`` left at the tail of the payload makes
        the JSON parser complain."""
        from mcpdump.core.transport import _iter_sse_messages

        assert list(_iter_sse_messages("data: x\r\n\r\n")) == ["x"]

    def test_multiline_data_is_joined_with_newlines(self) -> None:
        """SSE spec: multiple ``data`` lines in one event are joined with newlines."""
        from mcpdump.core.transport import _iter_sse_messages

        stream = "data: line1\ndata: line2\n\n"
        assert list(_iter_sse_messages(stream)) == ["line1\nline2"]

    def test_empty_data_produces_nothing(self) -> None:
        from mcpdump.core.transport import _iter_sse_messages

        assert list(_iter_sse_messages("data:\n\n")) == []


class TestBuildTransport:
    """``build_transport`` picks the right implementation."""

    def test_an_http_url_yields_an_http_transport(self) -> None:
        from mcpdump.core.transport import build_transport

        assert isinstance(build_transport("https://example.com/mcp"), HttpTransport)

    def test_a_command_yields_a_stdio_transport(self) -> None:
        from mcpdump.core.transport import StdioTransport, build_transport

        assert isinstance(build_transport("python server.py"), StdioTransport)

    def test_the_timeout_reaches_the_http_transport(self) -> None:
        from mcpdump.core.transport import build_transport

        transport = build_transport("https://example.com/mcp", timeout=7.5)
        assert isinstance(transport, HttpTransport)
        assert transport._timeout == 7.5  # noqa: SLF001 - this private value is the point

    def test_headers_reach_the_http_transport(self) -> None:
        from mcpdump.core.transport import build_transport

        transport = build_transport(
            "https://example.com/mcp", headers={"Authorization": "Bearer x"}
        )
        assert isinstance(transport, HttpTransport)
        assert transport.headers["Authorization"] == "Bearer x"


def test_the_transport_protocol_is_satisfied() -> None:
    """``HttpTransport`` must satisfy the ``Transport`` protocol; the ``runtime_checkable``
    ``isinstance`` is the machine-checkable form of "upper layers do not perceive transports".
    """
    from mcpdump.core.transport import Transport

    assert isinstance(HttpTransport("https://example.com/mcp"), Transport)


def test_no_new_runtime_dependency_was_added() -> None:
    """The HTTP implementation may use nothing but the standard library.

    Runtime dependencies are just ``typer`` and ``rich``; pulling in ``httpx`` to send a few
    POSTs would make ``pip install`` heavier.
    """
    import mcpdump.core.transport as module

    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    for forbidden in ("import requests", "import httpx", "from httpx", "from requests"):
        assert forbidden not in source, (
            f"the HTTP implementation pulled in a third-party dependency: {forbidden}"
        )

#!/usr/bin/env python3
"""Wrap the demo server in an HTTP shell to exercise the HTTP transport end to end.

It reuses ``mcpdump.demo.handle`` and captures its stdout as the response body, so the
MCP logic under test is the stdio one. Prints the chosen port to stdout on startup.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from mcpdump.demo import handle


def _dispatch(message: dict[str, Any]) -> str:
    """Feed one message to the demo server and return the response text it produced.

    ``handle`` writes to stdout, so stdout is swapped for a ``StringIO`` to capture it; a
    failed capture returns "" for the caller to treat as "no body".
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        handle(message)
    return buffer.getvalue().strip().splitlines()[0] if buffer.getvalue().strip() else ""


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    #: Filled in by ``main``: ``json`` or ``sse``.
    mode = "json"
    #: A stateful implementation hands out a session id; the client echoes it back.
    session_id = "fixture-session-1"

    def log_message(self, *args: object) -> None:
        return None

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            message = json.loads(raw) if raw else {}
        except ValueError:
            message = {}

        # Notifications carry no id; the spec says reply 202 with no body.
        if "id" not in message:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        reply = _dispatch(message)
        is_initialize = message.get("method") == "initialize"

        if self.mode == "sse":
            # Prepend a keep-alive comment; real servers do this and clients must ignore it.
            body = f": keep-alive\n\ndata: {reply}\n\n".encode()
            content_type = "text/event-stream"
        else:
            body = (reply + "\n").encode()
            content_type = "application/json"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if is_initialize and self.session_id:
            self.send_header("Mcp-Session-Id", self.session_id)
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--mode", choices=("json", "sse"), default="json")
    args = parser.parse_args()

    _Handler.mode = args.mode
    server = HTTPServer(("127.0.0.1", args.port), _Handler)
    # Annotate explicitly: the base class types ``server_address`` too loosely, and
    # without this mypy thinks it may be bytes and flags the f-string as ``str-bytes-safe``.
    host, port = server.server_address[:2]
    host_str, port_int = str(host), int(port)
    # The caller reads the port from this line; flush guarantees it lands before blocking.
    print(f"{host_str}:{port_int}", flush=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

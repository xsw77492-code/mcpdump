"""MCP session: handshake, capability discovery, tool calls.

This is where mcpdump stops being a generic JSON-RPC client: ``initialize``
first, then ``notifications/initialized``, and only call methods the server
declared a capability for.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, cast

from .. import DEFAULT_PROTOCOL_VERSION, __version__
from ..i18n import capability_label, t
from .jsonrpc import (
    METHOD_NOT_FOUND,
    JsonRpcError,
    ProtocolError,
    decode,
    encode_error_response,
    encode_notification,
    encode_request,
    is_notification,
    is_request,
    is_response,
    unwrap,
)
from .transport import Transport

CLIENT_INFO = {"name": "mcpdump", "version": __version__}
CLIENT_CAPABILITIES: dict[str, Any] = {"roots": {"listChanged": True}, "sampling": {}}

#: Display order for known capabilities. Fixed rather than taken from the
#: server's declaration so that output for one server is stable across runs.
CAPABILITY_ORDER: tuple[str, ...] = (
    "tools",
    "resources",
    "prompts",
    "logging",
    "completions",
    "experimental",
)


def initialize_params(
    protocol_version: str = DEFAULT_PROTOCOL_VERSION,
) -> dict[str, Any]:
    """Parameters for the ``initialize`` request.

    Shared with raw connections that drive the handshake themselves.
    """
    return {
        "protocolVersion": protocol_version,
        "capabilities": CLIENT_CAPABILITIES,
        "clientInfo": CLIENT_INFO,
    }


@dataclass
class ServerInfo:
    """The server's self-description, as returned by the initialize handshake."""

    name: str = ""
    version: str = ""
    title: str | None = None
    protocol_version: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    instructions: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def supports(self, capability: str) -> bool:
        """Whether the server declared a capability: 'tools', 'resources', 'prompts'."""
        return capability in self.capabilities

    def describe_capabilities(self) -> list[str]:
        """Turn the capabilities dict into display names.

        Known capabilities follow ``CAPABILITY_ORDER``; unrecognised ones are
        appended as-is.
        """
        known = [name for name in CAPABILITY_ORDER if name in self.capabilities]
        extra = [name for name in self.capabilities if name not in CAPABILITY_ORDER]
        return [capability_label(name) for name in (*known, *extra)]


@dataclass
class Exchange:
    """One request/response round trip, used by --trace and watch replay."""

    method: str
    request_line: str
    response_line: str | None
    elapsed_ms: float
    error: str | None = None


class MCPSession:
    """A single MCP session. Used as a context manager, it handshakes and closes."""

    def __init__(
        self,
        transport: Transport,
        *,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
        timeout: float = 30.0,
        trace: bool = False,
    ) -> None:
        self._transport = transport
        self._protocol_version = protocol_version
        self._timeout = timeout
        self._trace_enabled = trace
        self._next_id = 1
        self._exchanges: list[Exchange] = []
        self.server = ServerInfo()
        #: Non-JSON-RPC text seen on stdout (banners, logs). A protocol violation.
        self.non_protocol_output: list[str] = []
        #: Valid notifications sent by the server (JSON-RPC without an id).
        self.server_notifications: list[str] = []
        #: Requests the server initiated (JSON-RPC with an id). Always refused.
        self.server_requests: list[str] = []

    # ---- lifecycle ----

    def __enter__(self) -> MCPSession:
        self._transport.start()
        self.initialize()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._transport.close()

    # ---- handshake ----

    def initialize(self) -> ServerInfo:
        result = self._call("initialize", initialize_params(self._protocol_version))
        server_info = result.get("serverInfo") or {}
        self.server = ServerInfo(
            name=server_info.get("name", ""),
            version=server_info.get("version", ""),
            title=server_info.get("title"),
            protocol_version=result.get("protocolVersion", ""),
            capabilities=result.get("capabilities") or {},
            instructions=result.get("instructions"),
            raw=result,
        )
        self._notify("notifications/initialized")
        return self.server

    # ---- capability discovery ----

    def list_tools(self) -> list[dict[str, Any]]:
        if not self.server.supports("tools"):
            return []
        return self._call("tools/list", {}).get("tools", []) or []

    def list_resources(self) -> list[dict[str, Any]]:
        if not self.server.supports("resources"):
            return []
        return self._call("resources/list", {}).get("resources", []) or []

    def list_resource_templates(self) -> list[dict[str, Any]]:
        if not self.server.supports("resources"):
            return []
        return self._call("resources/templates/list", {}).get("resourceTemplates", []) or []

    def list_prompts(self) -> list[dict[str, Any]]:
        if not self.server.supports("prompts"):
            return []
        return self._call("prompts/list", {}).get("prompts", []) or []

    # ---- calls ----

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._call_object("tools/call", {"name": name, "arguments": arguments or {}})

    def read_resource(self, uri: str) -> dict[str, Any]:
        return self._call_object("resources/read", {"uri": uri})

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send an arbitrary JSON-RPC request and return its ``result``.

        Used by the conformance checks to probe methods the higher-level
        wrappers never touch, such as calling a non-existent tool.
        """
        return self._call(method, params)

    # ---- observation ----

    @property
    def exchanges(self) -> list[Exchange]:
        return list(self._exchanges)

    def last_exchange(self, method: str | None = None) -> Exchange | None:
        """The most recent round trip, or the most recent one for ``method``.

        ``Exchange.response_line`` is the raw message the server returned,
        never reordered or re-serialised.
        """
        if method is None:
            return self._exchanges[-1] if self._exchanges else None
        for exchange in reversed(self._exchanges):
            if exchange.method == method:
                return exchange
        return None

    # ---- internals ----

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._transport.send(encode_notification(method, params))

    def _call_object(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """``_call`` for the methods whose ``result`` is an object by spec.

        Asserting it here keeps ``Any`` from leaking into the public signatures;
        a server returning the wrong shape is reported by the result-shape check.
        """
        return cast(dict[str, Any], self._call(method, params))

    def _call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        msg_id = self._next_id
        self._next_id += 1
        request_line = encode_request(msg_id, method, params)
        started = time.perf_counter()
        self._transport.send(request_line)

        while True:
            line = self._transport.recv(timeout=self._timeout)
            if line is None:
                # Only a timeout lands here; a dead server raises ServerGoneError
                # with its exit code instead of making the caller wait it out.
                raise TimeoutError(t("session.timeout", method=method, timeout=self._timeout))
            try:
                msg = decode(line)
            except ProtocolError:
                # Non-protocol output on stdout; record it and keep waiting.
                self.non_protocol_output.append(line)
                continue

            if is_response(msg) and msg.get("id") == msg_id:
                elapsed = (time.perf_counter() - started) * 1000
                error = None
                try:
                    result = unwrap(msg)
                except JsonRpcError as exc:
                    error = str(exc)
                    self._exchanges.append(
                        Exchange(method, request_line, line, elapsed, error)
                    )
                    raise
                self._exchanges.append(Exchange(method, request_line, line, elapsed))
                return result

            if is_request(msg):
                # A server-initiated request (sampling/createMessage, roots/list).
                # Not implemented, but leaving it unanswered blocks the server
                # and every later call; the error text stays English on purpose.
                self.server_requests.append(line)
                self._transport.send(encode_error_response(
                    msg["id"], METHOD_NOT_FOUND, "mcpdump does not implement this request."
                ))
                continue

            if is_notification(msg):
                self.server_notifications.append(line)
                continue

            # What is left can only be a response with an unmatched id. Odd, but
            # not droppable: like a notification, it is unrelated server traffic.
            self.server_notifications.append(line)

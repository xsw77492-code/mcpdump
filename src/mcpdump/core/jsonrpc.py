"""JSON-RPC 2.0 codec.

Owned here rather than imported so every byte of the raw line stays visible.
Batch requests are not used by MCP, so they are rejected rather than ignored.
"""

from __future__ import annotations

import json
from typing import Any

from ..i18n import t

JSONRPC_VERSION = "2.0"

#: Standard JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class JsonRpcError(Exception):
    """The error object returned by the server."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            out["data"] = self.data
        return out


class ProtocolError(Exception):
    """A received message violates JSON-RPC or MCP."""


def encode_request(msg_id: int | str, method: str, params: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "method": method}
    if params is not None:
        payload["params"] = params
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def encode_notification(method: str, params: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        payload["params"] = params
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def encode_error_response(msg_id: int | str, code: int, message: str) -> str:
    """Build an error response, used to reject server-initiated requests we do not implement."""
    payload: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": msg_id,
        "error": {"code": code, "message": message},
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def decode(line: str) -> dict[str, Any]:
    """Parse one line into a JSON-RPC message. Raises ProtocolError with the raw text."""
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(t("jsonrpc.invalid_json", error=line[:200])) from exc

    if not isinstance(msg, dict):
        raise ProtocolError(t("jsonrpc.not_an_object", kind=type(msg).__name__))
    if msg.get("jsonrpc") != JSONRPC_VERSION:
        raise ProtocolError(t("jsonrpc.bad_version", value=msg.get("jsonrpc")))
    return msg


def is_request(msg: dict[str, Any]) -> bool:
    return "method" in msg and "id" in msg


def is_notification(msg: dict[str, Any]) -> bool:
    return "method" in msg and "id" not in msg


def is_response(msg: dict[str, Any]) -> bool:
    return "id" in msg and ("result" in msg or "error" in msg)


def unwrap(msg: dict[str, Any]) -> Any:
    """Return the result of a response, raising JsonRpcError if it carries an error."""
    if "error" in msg:
        err = msg["error"] or {}
        raise JsonRpcError(
            code=int(err.get("code", INTERNAL_ERROR)),
            message=str(err.get("message") or t("jsonrpc.no_error_message")),
            data=err.get("data"),
        )
    return msg.get("result")

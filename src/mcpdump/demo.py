"""The sample MCP server bundled with mcpdump -- ``python -m mcpdump.demo``.

Standard library only, no mcpdump imports: this is the known-good server the
conformance checks run against. It lives in the package so it runs from a PyPI
install, where no repository checkout is available. Connect with::

    mcpdump ls "python -m mcpdump.demo"
    mcpdump call "python -m mcpdump.demo" echo --args '{"text":"hello"}'
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any

#: The protocol version this server declares. Independent of the client's
#: default: the server reports what it supports, the client decides.
PROTOCOL_VERSION = "2025-06-18"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "echo",
        "title": "Echo",
        "description": "Returns the input text unchanged. Use it to verify the link is alive.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to echo back"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "title": "Add",
        "description": "Adds two numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "First addend"},
                "b": {"type": "number", "description": "Second addend"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "boom",
        "title": "Fails on purpose",
        "description": "Always returns isError=true; used to test mcpdump's error rendering.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "echo://readme",
        "name": "README",
        "description": "A sample resource that returns a fixed string.",
        "mimeType": "text/plain",
    }
]

PROMPTS: list[dict[str, Any]] = [
    {
        "name": "greet",
        "description": "Generates a greeting.",
        "arguments": [{"name": "who", "description": "Who to greet", "required": True}],
    }
]


def _send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(msg_id: object, result: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id: object, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _missing(arguments: dict[str, Any], names: tuple[str, ...]) -> str | None:
    """Return the first missing required argument name, or None if all are present.

    A missing argument must produce -32602, not a default value that lets the
    request complete -- the caller would believe the argument took effect.
    """
    for name in names:
        if name not in arguments:
            return name
    return None


def handle(msg: dict[str, Any]) -> None:
    """Process one already-parsed JSON-RPC message and write the response to stdout.

    Notifications (no ``id``) must not be answered, per the spec, which is why the
    trailing "Method not found" is guarded by ``msg_id is not None``.
    """
    method = msg.get("method")
    msg_id = msg.get("id")
    params: dict[str, Any] = msg.get("params") or {}

    if method == "initialize":
        _result(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {
                "name": "echo-server",
                "version": "0.1.0",
                "title": "mcpdump sample server",
            },
            "instructions": (
                "A sample MCP server for testing mcpdump, "
                "exposing the echo / add / boom tools."
            ),
        })
        return

    if method == "notifications/initialized":
        return

    if method == "tools/list":
        _result(msg_id, {"tools": TOOLS})
        return

    if method == "tools/call":
        name = params.get("name")
        arguments: dict[str, Any] = params.get("arguments") or {}
        if name == "echo":
            missing = _missing(arguments, ("text",))
            if missing:
                _error(msg_id, -32602, f"missing required argument: {missing}")
                return
            _result(msg_id, _text_result(str(arguments["text"])))
        elif name == "add":
            missing = _missing(arguments, ("a", "b"))
            if missing:
                _error(msg_id, -32602, f"missing required argument: {missing}")
                return
            try:
                total = float(arguments["a"]) + float(arguments["b"])
            except (TypeError, ValueError):
                _error(msg_id, -32602, "arguments a and b must be numbers")
                return
            _result(msg_id, _text_result(str(total)))
        elif name == "boom":
            _result(msg_id, _text_result("This failure is intentional.", is_error=True))
        else:
            _error(msg_id, -32602, f"unknown tool: {name}")
        return

    if method == "resources/list":
        _result(msg_id, {"resources": RESOURCES})
        return

    if method == "resources/read":
        uri = params.get("uri")
        if uri != "echo://readme":
            _error(msg_id, -32602, f"unknown resource: {uri}")
            return
        _result(msg_id, {
            "contents": [{"uri": uri, "mimeType": "text/plain", "text": "A sample resource."}]
        })
        return

    if method == "prompts/list":
        _result(msg_id, {"prompts": PROMPTS})
        return

    if msg_id is not None:
        _error(msg_id, -32601, f"Method not found: {method}")


def main() -> None:
    """Read JSON-RPC from stdin line by line until the stream ends.

    On Windows ``sys.stdin`` decodes as GBK by default and a CJK argument raises
    ``UnicodeDecodeError`` outright. ``isinstance`` rather than ``hasattr``:
    redirected or proxied standard streams are not necessarily ``TextIOWrapper``,
    and then there is no ``reconfigure`` to call.
    """
    if isinstance(sys.stdin, io.TextIOWrapper):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle(msg)


if __name__ == "__main__":
    main()

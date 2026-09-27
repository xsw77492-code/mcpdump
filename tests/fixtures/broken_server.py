#!/usr/bin/env python3
"""An intentionally broken MCP server: mcpdump check must report every problem.

With ``--faults=none`` it is fully compliant, so a failing check cannot be blamed on the
fixture; every other fault toggles one violation. Run with no argument for all of them.
"""

from __future__ import annotations

import json
import sys
from typing import Any

PROTOCOL_VERSION = "2025-06-18"

#: Every fault name, one per check, in check order.
ALL_FAULTS: tuple[str, ...] = (
    "no-protocol-version",           # 1
    "request-before-initialized",    # 2
    "undeclared-capability",         # 3
    "schema-not-object",             # 4
    "required-unknown-name",         # 5
    "unknown-tool-wrong-code",       # 6
    "invalid-params-succeeds",       # 7
    "content-not-array",             # 8
    "is-error-not-bool",             # 9
    "stdout-banner",                 # 10
)

BANNER = "broken-server 0.0.1 ready — this line is not JSON-RPC"

FAULTS: set[str] = set()

_TOOLS: list[dict[str, Any]] = []
_TOOLS_BY_NAME: dict[str, dict[str, Any]] = {}


def _build_tools(faults: set[str]) -> list[dict[str, Any]]:
    tools = [
        {
            "name": "ok",
            "description": "不需要参数，正常返回。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "needs-arg",
            "description": "有一个必需参数。",
            "inputSchema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        },
    ]
    if "schema-not-object" in faults:
        tools.append({
            "name": "bad-schema",
            "description": "inputSchema 不是对象。",
            "inputSchema": "this-should-be-an-object",
        })
    if "required-unknown-name" in faults:
        tools.append({
            "name": "bad-required",
            "description": "required 里有一个 properties 中不存在的字段。",
            "inputSchema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value", "ghost"],
            },
        })
    return tools


def _capabilities(faults: set[str]) -> dict[str, Any]:
    caps: dict[str, Any] = {
        "tools": {"listChanged": False},
        "prompts": {"listChanged": False},
    }
    if "undeclared-capability" not in faults:
        caps["resources"] = {"subscribe": False, "listChanged": False}
    return caps


def _send(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _result(msg_id: object, result: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id: object, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _tool_result(text: str) -> dict[str, Any]:
    """Tool result. Two faults each break the shape of ``content`` and ``isError``."""
    payload: dict[str, Any] = {}
    if "content-not-array" in FAULTS:
        payload["content"] = text
    else:
        payload["content"] = [{"type": "text", "text": text}]
    payload["isError"] = "false" if "is-error-not-bool" in FAULTS else False
    return payload


def _handle_tools_call(msg_id: object, params: dict[str, Any]) -> None:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    tool = _TOOLS_BY_NAME.get(name) if isinstance(name, str) else None
    if tool is None:
        if "unknown-tool-wrong-code" in FAULTS:
            _error(msg_id, -32601, f"Method not found: {name}")
        else:
            _error(msg_id, -32602, f"Unknown tool: {name}")
        return

    schema = tool.get("inputSchema")
    required = schema.get("required") if isinstance(schema, dict) else None
    for field in required or []:
        if field not in arguments and "invalid-params-succeeds" not in FAULTS:
            _error(msg_id, -32602, f"Missing required argument: {field}")
            return
    _result(msg_id, _tool_result(f"{name} ok"))


def handle(msg: dict[str, Any]) -> None:
    if "method" not in msg:
        return  # A response the client sent back to us (e.g. rejecting roots/list); ignore it.

    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        result: dict[str, Any] = {
            "capabilities": _capabilities(FAULTS),
            "serverInfo": {"name": "broken-server", "version": "0.0.1"},
        }
        if "no-protocol-version" not in FAULTS:
            result["protocolVersion"] = PROTOCOL_VERSION
        _result(msg_id, result)
        if "request-before-initialized" in FAULTS:
            # The violation: send a request before notifications/initialized arrives.
            _send({
                "jsonrpc": "2.0",
                "id": 9001,
                "method": "roots/list",
                "params": {},
            })
        return

    if method == "notifications/initialized":
        return

    if method == "tools/list":
        _result(msg_id, {"tools": _TOOLS})
        return

    if method == "tools/call":
        _handle_tools_call(msg_id, params)
        return

    if method == "resources/list":
        _result(msg_id, {"resources": []})
        return

    if method == "prompts/list":
        _result(msg_id, {"prompts": []})
        return

    if msg_id is not None:
        _error(msg_id, -32601, f"Method not found: {method}")


def _parse_faults(argv: list[str]) -> set[str]:
    for index, arg in enumerate(argv):
        value: str | None = None
        if arg.startswith("--faults="):
            value = arg.split("=", 1)[1]
        elif arg == "--faults" and index + 1 < len(argv):
            value = argv[index + 1]
        if value is None:
            continue
        text = value.strip()
        if text in ("", "all"):
            return set(ALL_FAULTS)
        if text == "none":
            return set()
        return {item.strip() for item in text.split(",") if item.strip()}
    return set(ALL_FAULTS)


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    global FAULTS, _TOOLS, _TOOLS_BY_NAME
    FAULTS = _parse_faults(sys.argv[1:])
    _TOOLS = _build_tools(FAULTS)
    _TOOLS_BY_NAME = {tool["name"]: tool for tool in _TOOLS}

    if "stdout-banner" in FAULTS:
        _send_banner()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle(msg)


def _send_banner() -> None:
    sys.stdout.write(BANNER + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()

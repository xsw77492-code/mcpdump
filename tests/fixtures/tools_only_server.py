#!/usr/bin/env python3
"""MCP server that declares only the tools capability.

When a server declares no resources/prompts, the client must not call resources/list or
prompts/list, which would be a protocol violation.
"""

from __future__ import annotations

import json
import sys

TOOLS = [
    {
        "name": "ping",
        "description": "返回 pong。",
        "inputSchema": {"type": "object", "properties": {}},
    }
]


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method")
        msg_id = msg.get("id")

        if method == "initialize":
            payload = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "tools-only", "version": "0.0.1"},
                },
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            payload = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            payload = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": "pong"}], "isError": False},
            }
        else:
            payload = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

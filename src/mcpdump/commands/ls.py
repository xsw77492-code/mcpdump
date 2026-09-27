"""mcpdump ls -- connect to any MCP server and list everything it offers.

This is the first command a user runs. It has one acceptance criterion: **from
pressing enter to seeing the result, the user must not be left with a question.**
"""

from __future__ import annotations

import json
from typing import Any

from ..core.jsonrpc import JsonRpcError
from ..exits import EXIT_ENVIRONMENT, EXIT_OK, EXIT_PROTOCOL, EXIT_TIMEOUT
from ..i18n import t
from ..runtime import SessionOptions, open_session
from ..ui import (
    console,
    display_width,
    err_console,
    hint_lines,
    render_error,
    render_next_steps,
    render_prompts,
    render_resources,
    render_server_header,
    render_tools,
    styled_lines,
)

#: Past this display width the hints switch to a placeholder. A full path (say a
#: Windows interpreter path) runs 60+ columns, and echoing it would wrap every
#: hint onto three lines, hiding what to type next.
_SERVER_PLACEHOLDER = "<SERVER>"
_SERVER_MAX_WIDTH = 40


def _next_step_lines(opts: SessionOptions, tools: list[dict[str, Any]]) -> list[str]:
    """Commands the user can copy directly. Empty when there are no tools, so no
    empty heading is printed."""
    names = [t.get("name", "") for t in tools if t.get("name")]
    if not names:
        return []
    server = opts.server
    if display_width(server) > _SERVER_MAX_WIDTH:
        server = _SERVER_PLACEHOLDER
    return hint_lines([
        f"mcpdump call {server} {names[0]} --args '{{}}'",
        f"mcpdump ls {server} --json",
    ])


def run(
    opts: SessionOptions,
    *,
    as_json: bool = False,
    verbose: bool = False,
    show_next: bool = True,
) -> int:
    # show_next is for demo, which runs three steps in a row; a "next steps"
    # block after each one is noise. On by default: for a lone mcpdump ls, that
    # block is the most useful part of the output.
    try:
        with open_session(opts) as session:
            server = session.server
            tools = session.list_tools()
            resources = session.list_resources()
            prompts = session.list_prompts()

            if as_json:
                payload: dict[str, Any] = {
                    "server": {
                        "name": server.name,
                        "version": server.version,
                        "protocolVersion": server.protocol_version,
                        "capabilities": sorted(server.capabilities),
                    },
                    "tools": tools,
                    "resources": resources,
                    "prompts": prompts,
                }
                # --json is machine-read: plain print, never Rich (it indents,
                # wraps, and highlights, which breaks parseability).
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return EXIT_OK

            render_server_header(console, server, opts.describe_transport())
            render_tools(console, tools, verbose=verbose)
            render_resources(console, resources)
            render_prompts(console, prompts)

            if not (tools or resources or prompts):
                err_console.print(styled_lines([(t("render.no_capabilities"), "mcpdump.warn")]))
            elif show_next:
                render_next_steps(console, _next_step_lines(opts, tools))
            return EXIT_OK

    except JsonRpcError as exc:
        render_error(err_console, exc)
        return EXIT_PROTOCOL
    except TimeoutError as exc:
        render_error(err_console, exc)
        return EXIT_TIMEOUT
    except RuntimeError as exc:
        render_error(err_console, exc)
        return EXIT_ENVIRONMENT

"""mcpdump call -- invoke a tool and print the full JSON-RPC round trip.

The workhorse for debugging. The key difference: raw request and response
frames are printed by default, not just a prettified result -- someone here
wants to know exactly what went over the wire.
"""

from __future__ import annotations

import difflib
import json

from ..core.jsonrpc import JsonRpcError
from ..exits import EXIT_ENVIRONMENT, EXIT_OK, EXIT_PROTOCOL, EXIT_TIMEOUT, EXIT_USAGE
from ..i18n import t
from ..runtime import SessionOptions, open_session
from ..ui import (
    console,
    err_console,
    render_error,
    render_tool_result,
    render_wire,
    styled_lines,
)


def _suggest(name: str, known: list[str]) -> str:
    close = difflib.get_close_matches(name, known, n=3, cutoff=0.4)
    if not close:
        return ""
    return t("call.suggestion", names=t("list.separator").join(close))


def _unknown_tool_lines(tool: str, available: list[str]) -> list[tuple[str, str]]:
    """Output for a nonexistent tool. The name comes from user input and the
    candidates from the server, so both count as external data."""
    lines = [(t("call.unknown_tool", tool=tool), "mcpdump.err")]
    hint = _suggest(tool, available)
    if hint:
        lines.append((hint, "mcpdump.dim"))
    if available:
        lines.append((
            t("call.available_tools", names=t("list.separator").join(available)),
            "mcpdump.dim",
        ))
    return lines


def run(
    opts: SessionOptions,
    tool: str,
    *,
    args: str | None = None,
    as_json: bool = False,
    show_wire: bool = True,
    full: bool = False,
) -> int:
    try:
        arguments = json.loads(args) if args else {}
    except json.JSONDecodeError as exc:
        err_console.print(styled_lines([
            (t("call.args_invalid", error=exc), "mcpdump.err"),
            (t("call.args_example"), "mcpdump.dim"),
        ]))
        return EXIT_USAGE

    if not isinstance(arguments, dict):
        err_console.print(styled_lines([(t("call.args_not_object"), "mcpdump.err")]))
        return EXIT_USAGE

    try:
        with open_session(opts) as session:
            available = [t.get("name", "") for t in session.list_tools()]

            if tool not in available:
                err_console.print(styled_lines(_unknown_tool_lines(tool, available)))
                return EXIT_USAGE

            result = session.call_tool(tool, arguments)

            if as_json:
                # --json is machine-read: plain print, never Rich (it indents,
                # wraps, and highlights, which breaks parseability).
                print(json.dumps(
                    {
                        "tool": tool,
                        "arguments": arguments,
                        "result": result,
                        "exchanges": [
                            {
                                "method": ex.method,
                                "request": ex.request_line,
                                "response": ex.response_line,
                                "elapsedMs": round(ex.elapsed_ms, 2),
                                "error": ex.error,
                            }
                            for ex in session.exchanges
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ))
                return EXIT_OK

            if show_wire:
                render_wire(console, session.exchanges, full=full)
                console.print()

            render_tool_result(console, result)
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

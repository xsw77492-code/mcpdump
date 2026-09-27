"""Runtime assembly shared by the commands: turn CLI arguments into a usable MCPSession.

This layer keeps each subcommand thin: concerned only with its own output, not
with transport selection, stderr relaying, or the trace switch.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import DEFAULT_PROTOCOL_VERSION
from .core import MCPSession, build_transport
from .ui import err_console, styled_line


@dataclass
class SessionOptions:
    """Every input to one session."""

    server: str
    protocol_version: str = DEFAULT_PROTOCOL_VERSION
    timeout: float = 30.0
    show_server_stderr: bool = True
    trace: bool = False
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    #: Pre-split launch arguments. Must be used when they come from a client
    #: config (``discover``): joining and re-splitting can break a path with
    #: spaces or backslashes, and breaking it reports no error.
    argv: list[str] | None = None

    def describe_transport(self) -> str:
        if self.server.startswith(("http://", "https://")):
            return f"Streamable HTTP · {self.server}"
        return f"stdio · {abbreviate_home(self.server)}"

    @property
    def launch_spec(self) -> str | list[str]:
        """Launch description handed to the transport: argv if present, else the
        server string."""
        return self.argv if self.argv else self.server


def abbreviate_home(text: str) -> str:
    """Replace the user's home directory with ~.

    A Windows interpreter path would otherwise blow out the capability panel and
    hide the protocol version and capability list. Both separators must be
    compared: ``expanduser`` returns backslashes on Windows while commands often
    arrive with forward slashes.
    """
    home = os.path.expanduser("~")
    if not home:
        return text
    for candidate in (home, home.replace("\\", "/")):
        if candidate in text:
            return text.replace(candidate, "~")
    return text


def default_stderr_handler(prefix: str = "server") -> Callable[[str], None]:
    """Relay the server's own stderr to mcpdump's stderr.

    Lives here rather than in ``core`` because it is rendering: a hand-written
    escape bypasses ``NO_COLOR`` and leaves ``^[[2m`` debris in redirected output.
    Stderr is often the only place a server crash explains itself.
    """

    def _handler(text: str) -> None:
        err_console.print(styled_line((f"[{prefix}] ", "mcpdump.dim"), (text, "mcpdump.dim")))

    return _handler


@contextmanager
def open_session(opts: SessionOptions) -> Iterator[MCPSession]:
    """Open one MCP session, guaranteeing the child process is reaped on exit."""

    def _trace(direction: str, line: str) -> None:
        # Frames routinely contain [ ]; this must go through Text, not a markup string
        style = "mcpdump.req" if direction == "->" else "mcpdump.res"
        err_console.print(styled_line((f"{direction} ", style), (line, "mcpdump.dim")))

    transport = build_transport(
        opts.launch_spec,
        env=opts.env or None,
        cwd=opts.cwd,
        on_stderr=default_stderr_handler() if opts.show_server_stderr else None,
        trace=_trace if opts.trace else None,
        # Must be passed explicitly: ``build_transport`` defaults to 30.0, so
        # omitting it voids the user's ``--timeout`` on the HTTP path, whose
        # socket timeout is separate from ``MCPSession``'s.
        timeout=opts.timeout,
    )
    session = MCPSession(
        transport,
        protocol_version=opts.protocol_version,
        timeout=opts.timeout,
        trace=opts.trace,
    )
    with session:
        yield session

"""mcpdump watch -- a transparent proxy: relay every frame, and display every frame.

**This command is itself a stdio MCP server.** A client launches
``mcpdump watch <real server>`` as its server; mcpdump launches the real server
and moves messages between the two directions.

One hard constraint follows: **stdout is the protocol channel**. A single
human-readable line written there breaks the client's JSON-RPC parser. Hence:

- stdout -- only raw frames relayed from the real server, bytes untouched.
- stderr -- the banner, the readable view of each frame, the server's own stderr,
  and the closing summary.

It differs from ``call`` in **timing**: ``call`` issues its own requests and
replays them afterwards; ``watch`` issues none, sitting in the middle and
mirroring someone else's conversation live. That client may be Claude Desktop,
Cursor, or another mcpdump.

**The exit code is the real server's exit code.** A proxy should not put its own
status on top of the server's. Only usage errors (1) and environment errors (4)
are exceptions, since at that point there is no server to exit.
"""

from __future__ import annotations

import json
from typing import Any

from ..core import Direction, ProxyEvent, StdioProxy, split_command
from ..exits import EXIT_ENVIRONMENT, EXIT_OK, EXIT_USAGE
from ..i18n import t
from ..runtime import abbreviate_home
from ..services.recorder import RecordedFrame, Recorder
from ..ui import (
    Frame,
    err_console,
    format_duration,
    render_error,
    render_frame,
    request_frame,
    response_frame,
    styled_line,
    styled_lines,
)

#: Fallback when no name can be guessed.
_FALLBACK_NAME = "mcp-server"

#: Script suffixes stripped when deriving a name from the launch command.
_SCRIPT_SUFFIXES = (".py", ".js", ".mjs", ".cjs", ".ts")


# ---------------------------------------------------------------- config generation


def _basename(token: str) -> str:
    """Reduce a token like ``@scope/pkg@1.2`` or ``C:\\x\\server.py`` to a short name."""
    name = token.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    for suffix in _SCRIPT_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.split("@", 1)[0] or name or _FALLBACK_NAME


def _suggest_name(argv: list[str]) -> str:
    """Guess a server name from the launch command.

    Takes the **first** non-option argument rather than the last: in
    ``npx -y @scope/pkg /tmp`` the rightmost token is a data directory, useless
    as a name. A wrong guess costs nothing -- the name is just a key in a config
    the user can edit.
    """
    for token in argv[1:]:
        if not token.startswith("-"):
            return _basename(token)
    return _basename(argv[0]) if argv else _FALLBACK_NAME


def client_config(server: str) -> dict[str, Any]:
    """Build a snippet that pastes straight into an MCP client config.

    The approach wraps the original command in ``mcpdump watch``: the client
    starts as usual with one extra proxy in between. No server code changes and
    no need for the user to remember how the proxy starts.

    The return type is ``dict[str, Any]`` rather than ``dict[str, object]``:
    ``record`` rewrites ``args`` inside this structure and ``object`` would make
    even indexing impossible. This is a mutable config structure, so ``Any`` is
    honest -- its shape is decided by MCP client conventions, not by this program.
    """
    argv = split_command(server)
    return {
        "mcpServers": {
            _suggest_name(argv): {"command": "mcpdump", "args": ["watch", *argv]},
        }
    }


# ---------------------------------------------------------------- recording


def _record(event: ProxyEvent) -> RecordedFrame:
    """The on-disk form of one frame.

    **The format is not defined here** -- it lives in ``services/recorder.py``,
    which ``replay`` and ``diff`` read from the same definition. This only
    translates ``ProxyEvent`` into ``RecordedFrame``. Defining it twice would
    guarantee that a field added on the recording side goes unnoticed on the
    playback side.
    """
    return RecordedFrame(
        seq=event.sequence,
        at_ms=round(event.at_ms, 3),
        direction=event.direction.value,
        method=event.method,
        elapsed_ms=None if event.elapsed_ms is None else round(event.elapsed_ms, 3),
        line=event.line,
    )


def _frame_of(event: ProxyEvent) -> Frame:
    """Translate a frame event into a renderable frame.

    Request frames carry no duration and response frames do -- the duration is
    the **whole round trip**, so attaching it to the request would be a lie.
    """
    if event.direction is Direction.TO_SERVER:
        return request_frame(event.method, event.line)
    return response_frame(event.method, event.line, elapsed_ms=event.elapsed_ms)


def _relay_server_stderr(text: str) -> None:
    err_console.print(styled_line(("[server] ", "mcpdump.dim"), (text, "mcpdump.dim")))


def _print_banner(server: str, record: str | None) -> None:
    """The opening message.

    The launch command gets its own line and is **not wrapped**: a Windows
    interpreter path runs to hundreds of characters, and wrapping inserts a hard
    newline mid-path that looks like a broken path and prevents copying the line
    whole.
    """
    lines = [
        (t("watch.banner"), "mcpdump.brand"),
        (f"  {t('watch.proxying', server=abbreviate_home(server))}", "mcpdump.dim"),
        (f"  {t('watch.channel_note')}", "mcpdump.dim"),
    ]
    if record:
        lines.append((f"  {t('watch.recording', path=abbreviate_home(record))}", "mcpdump.dim"))
    err_console.print(styled_lines(lines), soft_wrap=True)
    err_console.print()


def _print_summary(proxy: StdioProxy, recorder: Recorder | None, code: int) -> None:
    stats = proxy.stats
    lines: list[tuple[str, str]] = [
        (
            t(
                "watch.summary",
                to_server=stats.to_server,
                to_client=stats.to_client,
                duration=format_duration(stats.duration_ms),
            ),
            "mcpdump.meta",
        ),
    ]
    if stats.dropped:
        lines.append((t("watch.dropped", count=stats.dropped), "mcpdump.warn"))
    if stats.render_errors:
        lines.append((t("watch.render_errors", count=stats.render_errors), "mcpdump.warn"))
    if recorder is not None and recorder.error is not None:
        lines.append((
            t(
                "watch.record_broken",
                count=recorder.count,
                error=recorder.error.strerror or recorder.error,
            ),
            "mcpdump.warn",
        ))
    if code:
        lines.append((t("watch.exited", code=code), "mcpdump.warn"))
    err_console.print()
    err_console.print(styled_lines(lines), soft_wrap=True)


# ---------------------------------------------------------------- entry point


def run(
    server: str,
    *,
    record: str | None = None,
    print_config: bool = False,
    full: bool = False,
    quiet_server: bool = False,
) -> int:
    if print_config:
        # This branch launches nothing, so no protocol traffic reaches stdout and
        # a plain print is safe: the output must redirect into mcp.json verbatim,
        # which rules out Rich (it wraps, colours, and indents).
        print(json.dumps(client_config(server), ensure_ascii=False, indent=2))
        return EXIT_OK

    recorder: Recorder | None = None
    if record:
        try:
            # The launch command goes into the header: the first question anyone
            # reading the recording later asks is "which server was this?".
            recorder = Recorder(record, argv=tuple(split_command(server)))
        except OSError as exc:
            err_console.print(
                styled_lines([
                    (
                        t("watch.record_failed", path=record, error=exc.strerror or exc),
                        "mcpdump.err",
                    ),
                ]),
                soft_wrap=True,
            )
            return EXIT_USAGE

    def _on_event(event: ProxyEvent) -> None:
        # Recording precedes rendering: a render failure is swallowed by the
        # proxy (it must not interrupt relaying), but the recorded bytes have to
        # survive -- that is what --record is for.
        if recorder is not None:
            recorder.write(_record(event))
        render_frame(err_console, _frame_of(event), full=full)

    proxy = StdioProxy(
        server,
        on_event=_on_event,
        on_server_stderr=None if quiet_server else _relay_server_stderr,
    )

    _print_banner(server, record)
    code = EXIT_OK
    try:
        code = proxy.run()
    except RuntimeError as exc:
        render_error(err_console, exc)
        code = EXIT_ENVIRONMENT
    finally:
        proxy.close()
        _print_summary(proxy, recorder, code)
        if recorder is not None:
            recorder.close()
    return code

"""``mcpdump record`` -- wrap any MCP client and write the whole session to disk.

Shares one proxy with ``watch`` (``core.proxy.StdioProxy``); the difference is
**what is watched**:

- ``watch`` is for a human: renders each frame to stderr, optionally also recording.
- ``record`` is for a machine: writes to disk only, leaving one progress line on stderr.

**Why it is not simply ``watch --record --quiet``**: the semantics differ.
``watch --record`` means "also save a copy" and its subject is the live view;
``record``'s subject is the file, and a view would only be interference. As its
own command, ``record``'s output is clean for scripts -- like ``--print-config``,
no protocol traffic reaches stdout.

## stdout is still the protocol channel

Identical to ``watch``: the client launches ``mcpdump record <real server>`` as
its server. Progress, warnings, and the summary therefore all go to stderr, and
stdout carries only raw frames relayed from the real server.
"""

from __future__ import annotations

import json

from ..core import ProxyEvent, StdioProxy, split_command
from ..exits import EXIT_ENVIRONMENT, EXIT_OK, EXIT_USAGE
from ..i18n import t
from ..runtime import abbreviate_home
from ..services.recorder import RecordedFrame, Recorder
from ..ui import err_console, render_error, styled_line, styled_lines

__all__ = ["run"]


def _record(event: ProxyEvent) -> RecordedFrame:
    """Same translation ``watch`` uses. The format is defined in ``services/recorder.py``."""
    return RecordedFrame(
        seq=event.sequence,
        at_ms=round(event.at_ms, 3),
        direction=event.direction.value,
        method=event.method,
        elapsed_ms=None if event.elapsed_ms is None else round(event.elapsed_ms, 3),
        line=event.line,
    )


def _print_banner(path: str, server: str) -> None:
    err_console.print(styled_lines([
        (t("record.banner", path=abbreviate_home(path)), "mcpdump.brand"),
        (t("record.wrapping", server=abbreviate_home(server)), "mcpdump.dim"),
    ]))
    err_console.print()


def _print_summary(recorder: Recorder, code: int) -> None:
    lines: list[tuple[str, str]] = [
        (t("record.summary", count=recorder.count, path=abbreviate_home(recorder.path)),
         "mcpdump.meta"),
    ]
    if recorder.error is not None:
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


def _as_record_config(config: dict[str, object], out: str) -> dict[str, object]:
    """Rewrite the config ``watch`` produces into its ``record`` form.

    Explicit ``isinstance`` rather than ``assert``: ``-O`` strips asserts
    entirely, leaving this silently doing nothing -- far harder to trace than an
    exception. ``client_config`` guarantees the shape in theory, but that is
    **another module's** guarantee, and cross-module trust should not rest on an
    assertion.
    """
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        raise RuntimeError("client_config returned an unexpected shape")

    entry = next(iter(servers.values()), None)
    if not isinstance(entry, dict):
        raise RuntimeError("client_config returned an unexpected entry")

    args = entry.get("args")
    if not isinstance(args, list) or not args:
        raise RuntimeError("client_config returned no args")

    # ``args`` is ``["watch", *argv]``; replacing the first element yields record.
    entry["args"] = ["record", "--out", out, *args[1:]]
    return config


def run(
    server: str,
    *,
    out: str,
    quiet_server: bool = False,
    print_config: bool = False,
) -> int:
    if print_config:
        # Shares one implementation with watch's --print-config: one statement
        # per fact. This branch launches nothing, so no protocol traffic reaches
        # stdout and a plain print is safe.
        from .watch import client_config

        print(json.dumps(_as_record_config(client_config(server), out),
                         ensure_ascii=False, indent=2))
        return EXIT_OK

    try:
        recorder = Recorder(out, argv=tuple(split_command(server)))
    except OSError as exc:
        err_console.print(styled_lines([
            (t("record.open_failed", path=out, error=exc.strerror or exc), "mcpdump.err"),
        ]))
        return EXIT_USAGE

    def _on_event(event: ProxyEvent) -> None:
        # record does not render: its subject is the file. A render failure
        # would be swallowed by the proxy, but the recorded bytes must survive
        # -- that is why the user ran this command.
        recorder.write(_record(event))

    def _relay_stderr(text: str) -> None:
        # The server's stderr is still relayed: on a crash it is the only clue,
        # and suppressing it hides the most useful diagnostic available.
        err_console.print(styled_line(("[server] ", "mcpdump.dim"), (text, "mcpdump.dim")))

    proxy = StdioProxy(
        server,
        on_event=_on_event,
        on_server_stderr=None if quiet_server else _relay_stderr,
    )

    _print_banner(out, server)
    code = EXIT_OK
    try:
        code = proxy.run()
    except RuntimeError as exc:
        render_error(err_console, exc)
        code = EXIT_ENVIRONMENT
    finally:
        proxy.close()
        _print_summary(recorder, code)
        recorder.close()
    return code

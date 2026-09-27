"""mcpdump command-line entry point.

CLI conventions, true for every subcommand:
- The first positional argument is always SERVER: either a launch command or an
  http(s):// address. **The only exception is ``demo``**, which connects to
  mcpdump's built-in sample server, whose launch command depends on which
  interpreter is running mcpdump -- only ``demo`` knows that. Making it an argument
  would hand the one reliable source of that answer to the user to guess.
- Every command supports --json with a stable schema, for CI and scripts.
- Exit codes live in ``exits.py``, the contract's **only** definition; they are not
  repeated here.

**Running bare, with no arguments, goes to discover**: the first thing anyone wants
after installing mcpdump is "what have I got?". Listing their already-configured
servers is closer to "productive in 5 seconds" than dumping help. Help is shown only
when nothing is found -- and in that case it is genuinely what they need.

All help text goes through i18n. Typer reads ``help=`` when the decorator is
evaluated, so the language is fixed at import time -- ``MCPDUMP_LANG`` must be set
before the process starts, which is its normal usage anyway. Hence **no docstrings
here**: an explicit ``help=`` already takes precedence over a docstring, and keeping
both would suggest editing the docstring does something.

Parameters are always declared with ``Annotated``, **never with
``typer.Option(...)`` in the default-value position**. The latter hides the default
inside a function call: ``--client`` used to read
``typer.Option([], "--client")``, whose ``[]`` is mutable and shared across calls,
yet escaped ruff's B006 by hiding in a call. ``Annotated`` puts the default back in
the signature -- visible to readers and to linters.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from . import DEFAULT_PROTOCOL_VERSION, __version__
from .commands import call as call_cmd
from .commands import check as check_cmd
from .commands import demo as demo_cmd
from .commands import diff as diff_cmd
from .commands import discover as discover_cmd
from .commands import ls as ls_cmd
from .commands import mock as mock_cmd
from .commands import record as record_cmd
from .commands import replay as replay_cmd
from .commands import tui as tui_cmd
from .commands import watch as watch_cmd
from .exits import EXIT_INTERRUPTED
from .i18n import t
from .runtime import SessionOptions
from .ui import err_console, styled_lines

app = typer.Typer(
    name="mcpdump",
    help=t("app.about"),
    add_completion=False,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"mcpdump {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_version_callback, is_eager=True, help=t("opt.version")
        ),
    ] = False,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    # Bare run: check for already-configured servers first. List them if any, and
    # fall back to help only if none.
    if not discover_cmd.run_bare():
        typer.echo(ctx.get_help())


@app.command("demo", help=t("cmd.demo"))
def demo(
    cmd: Annotated[bool, typer.Option("--cmd", help=t("opt.demo_cmd"))] = False,
) -> None:
    # Registered before ls: this is the only command a new user can run without
    # knowing what to connect to, so it should be the first line of the help list.
    raise typer.Exit(demo_cmd.run(as_command=cmd))


@app.command("ls", help=t("cmd.ls"))
def ls(
    server: Annotated[str, typer.Argument(metavar="SERVER", help=t("arg.server"))],
    protocol_version: Annotated[
        str, typer.Option("--protocol-version", help=t("opt.protocol_version"))
    ] = DEFAULT_PROTOCOL_VERSION,
    timeout: Annotated[float, typer.Option("--timeout", min=0.1, help=t("opt.timeout"))] = 30.0,
    quiet_server: Annotated[
        bool, typer.Option("--quiet-server", help=t("opt.quiet_server"))
    ] = False,
    trace: Annotated[bool, typer.Option("--trace", help=t("opt.trace"))] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help=t("opt.verbose"))] = False,
    as_json: Annotated[bool, typer.Option("--json", help=t("opt.json"))] = False,
) -> None:
    opts = SessionOptions(
        server=server,
        protocol_version=protocol_version,
        timeout=timeout,
        show_server_stderr=not quiet_server,
        trace=trace,
    )
    raise typer.Exit(ls_cmd.run(opts, as_json=as_json, verbose=verbose))


@app.command("tui", help=t("cmd.tui"))
def tui(
    server: Annotated[str, typer.Argument(metavar="SERVER", help=t("arg.server"))],
    protocol_version: Annotated[
        str, typer.Option("--protocol-version", help=t("opt.protocol_version"))
    ] = DEFAULT_PROTOCOL_VERSION,
    timeout: Annotated[float, typer.Option("--timeout", min=0.1, help=t("opt.timeout"))] = 30.0,
    script: Annotated[
        str | None, typer.Option("--script", help=t("opt.script"), hidden=True)
    ] = None,
) -> None:
    # Deliberately no --quiet-server and no --trace: the view redraws in place on
    # stdout, so anything written to stderr shreds it. The command forces it off
    # again internally; omitting the switch here is **not offering a broken option**
    # -- the wire pane already covers what --trace does.
    opts = SessionOptions(
        server=server,
        protocol_version=protocol_version,
        timeout=timeout,
        show_server_stderr=False,
    )
    raise typer.Exit(tui_cmd.run(opts, script=script))


@app.command("call", help=t("cmd.call"))
def call(
    server: Annotated[str, typer.Argument(metavar="SERVER", help=t("arg.server"))],
    tool: Annotated[str, typer.Argument(metavar="TOOL", help=t("arg.tool"))],
    args: Annotated[str | None, typer.Option("--args", "-a", help=t("opt.args"))] = None,
    protocol_version: Annotated[
        str, typer.Option("--protocol-version", help=t("opt.protocol_version"))
    ] = DEFAULT_PROTOCOL_VERSION,
    timeout: Annotated[float, typer.Option("--timeout", min=0.1, help=t("opt.timeout"))] = 30.0,
    quiet_server: Annotated[
        bool, typer.Option("--quiet-server", help=t("opt.quiet_server"))
    ] = False,
    trace: Annotated[bool, typer.Option("--trace", help=t("opt.trace"))] = False,
    no_wire: Annotated[bool, typer.Option("--no-wire", help=t("opt.no_wire"))] = False,
    full: Annotated[bool, typer.Option("--full", help=t("opt.full"))] = False,
    as_json: Annotated[bool, typer.Option("--json", help=t("opt.json"))] = False,
) -> None:
    opts = SessionOptions(
        server=server,
        protocol_version=protocol_version,
        timeout=timeout,
        show_server_stderr=not quiet_server,
        trace=trace,
    )
    raise typer.Exit(
        call_cmd.run(opts, tool, args=args, as_json=as_json, show_wire=not no_wire, full=full)
    )


@app.command("check", help=t("cmd.check"))
def check(
    server: Annotated[str, typer.Argument(metavar="SERVER", help=t("arg.server"))],
    protocol_version: Annotated[
        str, typer.Option("--protocol-version", help=t("opt.protocol_version"))
    ] = DEFAULT_PROTOCOL_VERSION,
    timeout: Annotated[float, typer.Option("--timeout", min=0.1, help=t("opt.timeout"))] = 30.0,
    quiet_server: Annotated[
        bool, typer.Option("--quiet-server", help=t("opt.quiet_server"))
    ] = False,
    trace: Annotated[bool, typer.Option("--trace", help=t("opt.trace"))] = False,
    as_json: Annotated[bool, typer.Option("--json", help=t("opt.json"))] = False,
    badge: Annotated[bool, typer.Option("--badge", help=t("opt.badge"))] = False,
    markdown: Annotated[bool, typer.Option("--markdown", help=t("opt.markdown"))] = False,
) -> None:
    opts = SessionOptions(
        server=server,
        protocol_version=protocol_version,
        timeout=timeout,
        show_server_stderr=not quiet_server,
        trace=trace,
    )
    raise typer.Exit(check_cmd.run(opts, as_json=as_json, badge=badge, markdown=markdown))


@app.command("watch", help=t("cmd.watch"))
def watch(
    server: Annotated[str, typer.Argument(metavar="SERVER", help=t("arg.server"))],
    record: Annotated[str | None, typer.Option("--record", help=t("opt.record"))] = None,
    print_config: Annotated[
        bool, typer.Option("--print-config", help=t("opt.print_config"))
    ] = False,
    full: Annotated[bool, typer.Option("--full", help=t("opt.full"))] = False,
    quiet_server: Annotated[
        bool, typer.Option("--quiet-server", help=t("opt.quiet_server"))
    ] = False,
) -> None:
    # watch neither handshakes nor issues requests, so no SessionOptions is built --
    # it has no protocol version or timeout, and forcing them in would imply they
    # do something.
    raise typer.Exit(
        watch_cmd.run(
            server,
            record=record,
            print_config=print_config,
            full=full,
            quiet_server=quiet_server,
        )
    )


@app.command("discover", help=t("cmd.discover"))
def discover(
    use: Annotated[str | None, typer.Option("--use", metavar="NAME", help=t("opt.use"))] = None,
    client: Annotated[list[str] | None, typer.Option("--client", help=t("opt.client"))] = None,
    project: Annotated[str | None, typer.Option("--project", help=t("opt.project"))] = None,
    probe: Annotated[bool, typer.Option("--probe", help=t("opt.probe"))] = False,
    as_json: Annotated[bool, typer.Option("--json", help=t("opt.json"))] = False,
) -> None:
    raise typer.Exit(
        discover_cmd.run(
            as_json=as_json,
            use=use,
            clients=client,
            project=project,
            probe=probe,
        )
    )


@app.command("record", help=t("cmd.record"))
def record(
    server: Annotated[str, typer.Argument(metavar="SERVER", help=t("arg.server_wrap"))],
    out: Annotated[str, typer.Option("--out", "-o", help=t("opt.out"))] = "session.jsonl",
    quiet_server: Annotated[
        bool, typer.Option("--quiet-server", help=t("opt.quiet_server"))
    ] = False,
    print_config: Annotated[
        bool, typer.Option("--print-config", help=t("opt.print_config"))
    ] = False,
) -> None:
    raise typer.Exit(
        record_cmd.run(
            server,
            out=out,
            quiet_server=quiet_server,
            print_config=print_config,
        )
    )


@app.command("replay", help=t("cmd.replay"))
def replay(
    path: Annotated[str, typer.Argument(metavar="FILE", help=t("arg.file"))],
    as_json: Annotated[bool, typer.Option("--json", help=t("opt.json"))] = False,
    step: Annotated[bool, typer.Option("--step", help=t("opt.step"))] = False,
    limit: Annotated[int, typer.Option("--limit", min=0, help=t("opt.limit"))] = 200,
    wire: Annotated[bool, typer.Option("--wire", help=t("opt.wire"))] = False,
) -> None:
    raise typer.Exit(
        replay_cmd.run(path, as_json=as_json, step=step, limit=limit, show_wire=wire)
    )


@app.command("diff", help=t("cmd.diff"))
def diff(
    before: Annotated[str, typer.Argument(metavar="BEFORE", help=t("arg.record"))],
    after: Annotated[str, typer.Argument(metavar="AFTER", help=t("arg.record"))],
    as_json: Annotated[bool, typer.Option("--json", help=t("opt.json"))] = False,
) -> None:
    raise typer.Exit(diff_cmd.run(before, after, as_json=as_json))


@app.command("mock", help=t("cmd.mock"))
def mock(
    path: Annotated[
        str | None, typer.Argument(metavar="FILE", help=t("arg.record_jsonl"))
    ] = None,
    from_server: Annotated[
        str | None, typer.Option("--from", metavar="SERVER", help=t("opt.from"))
    ] = None,
    out: Annotated[str | None, typer.Option("--out", "-o", help=t("opt.mock_out"))] = None,
    max_requests: Annotated[
        int, typer.Option("--max-requests", min=0, help=t("opt.max_requests"))
    ] = 0,
    protocol_version: Annotated[
        str, typer.Option("--protocol-version", help=t("opt.protocol_version"))
    ] = DEFAULT_PROTOCOL_VERSION,
    timeout: Annotated[float, typer.Option("--timeout", min=0.1, help=t("opt.timeout"))] = 30.0,
    quiet_server: Annotated[
        bool, typer.Option("--quiet-server", help=t("opt.quiet_server"))
    ] = False,
) -> None:
    # FILE and --from are mutually exclusive. **Checked here, not in the command
    # implementation**: this is a usage error, and Typer has a proper channel for
    # those (exit code 2 plus a help hint). Raising our own exception would turn it
    # into an error that no longer reads as a usage problem.
    if (path is None) == (from_server is None):
        raise typer.BadParameter(t("mock.need_one_source"))

    opts = None
    if from_server is not None:
        # ``--from`` supplies a default filename: recording must work without
        # --out, or the user has to think up one more parameter name every time.
        out = out or "mock-session.jsonl"
        opts = SessionOptions(
            server=from_server,
            protocol_version=protocol_version,
            timeout=timeout,
            show_server_stderr=not quiet_server,
        )

    # As with watch: mock does not handshake itself (it is the one being
    # handshaken), so it has no protocol version or timeout -- only the --from path
    # needs those.
    raise typer.Exit(
        mock_cmd.run(
            path,
            from_server=opts,
            out=out,
            max_requests=max_requests,
        )
    )


def main() -> None:
    # Force UTF-8 on the standard streams. On Windows the console code page
    # (cp1252 by default) cannot encode the Unicode symbols this program prints
    # (▸, ✓, …), so `mcpdump ls > file` or a non-UTF-8 pipe would raise
    # UnicodeEncodeError. ``reconfigure`` is a no-op on real terminals and on
    # streams already in UTF-8; it only matters where the default is wrong.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):  # not a text stream, or already closed
                pass
    try:
        app()
    except KeyboardInterrupt:
        err_console.print(styled_lines([(t("err.interrupted"), "mcpdump.dim")]))
        raise SystemExit(EXIT_INTERRUPTED) from None


if __name__ == "__main__":
    main()

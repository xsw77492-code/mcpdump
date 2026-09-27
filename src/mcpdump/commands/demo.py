"""mcpdump demo -- run the whole toolchain against the built-in sample server.

**What it solves: the README's first command must work on an install-only setup.**
Anyone installing mcpdump from PyPI has no access to the repo's ``examples/``, and
``uvx`` / ``pipx`` users have no "python that has mcpdump installed" at all --
the ``python`` on PATH is a different interpreter, so ``python -m mcpdump.demo``
raises ``No module named mcpdump``.

**Hence ``sys.executable`` for the launch command, passed as ``argv`` rather than
a joined string.** ``sys.executable`` always points at the interpreter running
mcpdump, which necessarily has mcpdump importable (including inside uvx's
temporary environment). ``argv`` is used because Windows interpreter paths contain
backslashes; joining them into a string and letting ``split_command`` split it
again breaks any path with a space -- see the note on ``SessionOptions.argv``,
which records the same pitfall.

**All three scenarios really run**: they call the ``run()`` of ``ls`` / ``call`` /
``check`` rather than duplicating rendering. What the user sees here is what those
three commands print -- a demo that disagrees with reality is worse than none.

**No fully copyable command at the end**, for the same reason as ``discover``:
the path in it may contain spaces, and wrapping it in more quotes means bash and
cmd need different escaping, so a paste is likely broken. A command that looks
copyable but breaks on paste is worse than a bare command name, with ``--cmd``
available for anyone who wants the real thing.
"""

from __future__ import annotations

import sys

from ..exits import EXIT_OK
from ..i18n import t
from ..runtime import SessionOptions
from ..ui import console, err_console, hint_lines, justify_ends, render_next_steps, styled_lines
from . import call as call_cmd
from . import check as check_cmd
from . import ls as ls_cmd

#: Tool and arguments the demo calls. Deliberately trivial input: the point is
#: showing what frames look like, not exercising argument parsing. The text is
#: English so that a non-UTF-8 terminal does not render it as a garbled mess
#: that reads like a failure.
DEMO_TOOL = "echo"
DEMO_ARGS = '{"text": "hello from mcpdump"}'

#: The built-in server only computes, so the handshake takes milliseconds. The
#: 30 seconds are there so a genuinely stuck machine reports a timeout instead of
#: waiting forever.
TIMEOUT = 30.0


#: The line shown in the server card. Deliberately **not** the full
#: ``sys.executable`` path: at 100+ characters it would push the card to three
#: lines and hide the capability list that matters. Launching uses ``argv`` and
#: ``--cmd`` hands out yet another full path -- each serves its own purpose.
DISPLAY_COMMAND = "python -m mcpdump.demo"


def server_command() -> str:
    """A launch command that can be pasted into a shell; the output of ``--cmd``.

    It must be **genuinely runnable**, hence the interpreter's absolute path:
    the ``python`` on PATH is not necessarily the one with mcpdump installed
    (``pipx`` / ``uvx`` / virtual environments all make it miss), and a miss
    reports ``No module named 'mcpdump'``, which looks unrelated to installation.
    """
    return f"{sys.executable} -m mcpdump.demo"


def options() -> SessionOptions:
    """Session options for connecting to the built-in sample server."""
    return SessionOptions(
        server=DISPLAY_COMMAND,
        argv=[sys.executable, "-m", "mcpdump.demo"],
        timeout=TIMEOUT,
        # The built-in server should write nothing to stderr; if it did, mixing
        # that into the display would read as a failure.
        show_server_stderr=False,
    )


def _intro() -> None:
    console.print(styled_lines([
        (t("demo.title"), "mcpdump.label"),
        ("", ""),
        (t("demo.blurb"), "mcpdump.dim"),
    ]))


def _step(number: int, question: str, command: str) -> None:
    """The heading bar for one step: the question it answers on the left, the
    equivalent command on the right.

    Putting the command in the heading lines up "what I see" with "what I should
    type", so anyone wanting to try it afterwards need not go back to the docs.
    """
    width = console.size.width
    console.print()
    console.print(styled_lines([
        (justify_ends(f"{number}. {question}", command, width=width), "mcpdump.label"),
        ("─" * width, "mcpdump.dim"),
    ]))


def _failed(code: int) -> int:
    """The built-in server failing means mcpdump itself is broken. Say so plainly
    rather than sending the user off to inspect their environment."""
    err_console.print()
    err_console.print(styled_lines([(t("demo.failed"), "mcpdump.err")]))
    return code


def _outro() -> None:
    render_next_steps(console, hint_lines([
        t("demo.next.cmd"),
        t("demo.next.tui"),
        t("demo.next.discover"),
    ]))


def run(*, as_command: bool = False) -> int:
    """Run the demo; ``as_command`` prints only the launch command.

    ``--cmd`` output must use plain ``print``, not Rich: it is meant for
    ``$(...)`` or direct pasting as an argument, and added indentation or colour
    would corrupt it. Same rule as ``--json``.
    """
    if as_command:
        print(server_command())
        return EXIT_OK

    opts = options()
    _intro()

    _step(1, t("demo.step.ls"), "mcpdump ls")
    # Disable ls's own "next steps" block: step 2 follows immediately and two
    # consecutive Next blocks would be noise.
    code = ls_cmd.run(opts, show_next=False)
    if code != EXIT_OK:
        return _failed(code)

    _step(2, t("demo.step.call"), f"mcpdump call <SERVER> {DEMO_TOOL}")
    code = call_cmd.run(opts, DEMO_TOOL, args=DEMO_ARGS)
    if code != EXIT_OK:
        return _failed(code)

    _step(3, t("demo.step.check"), "mcpdump check")
    code = check_cmd.run(opts)
    if code != EXIT_OK:
        return _failed(code)

    _outro()
    return EXIT_OK


__all__ = [
    "DEMO_ARGS",
    "DEMO_TOOL",
    "DISPLAY_COMMAND",
    "TIMEOUT",
    "options",
    "run",
    "server_command",
]

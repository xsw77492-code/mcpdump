"""Tests for ``mcpdump demo``.

It is the first output a new user sees, so this pins two fatal things: the launch command
goes through ``argv`` (string concatenation mangles paths with spaces), and step failures
propagate their exit code verbatim.
"""

from __future__ import annotations

import io
import sys
from typing import Any

import pytest
from rich.console import Console

from mcpdump.cli import app
from mcpdump.commands import call as call_cmd
from mcpdump.commands import check as check_cmd
from mcpdump.commands import demo as demo_cmd
from mcpdump.commands import ls as ls_cmd
from mcpdump.exits import EXIT_NOT_CONFORMANT, EXIT_OK


def _console() -> tuple[Console, io.StringIO]:
    """An offscreen Console, plus the buffer it writes to.

    ``_environ={}`` and ``legacy_windows=False`` are explicit: otherwise the developer's
    ``TERM`` is read and the width differs per platform, shifting the wrap points.
    """
    stream = io.StringIO()
    console = Console(file=stream, width=100, height=30, _environ={}, legacy_windows=False)
    return console, stream


def _quiet(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Route the demo's output into memory so it does not drown pytest's summary line."""
    console, stream = _console()
    monkeypatch.setattr(demo_cmd, "console", console)
    monkeypatch.setattr(demo_cmd, "err_console", console)
    return stream


def _stub_steps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_at: str | None = None,
) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
    """Swap the three subcommands for recorders.

    This exercises demo's own orchestration (order, arguments, failure propagation) rather
    than re-testing ``ls`` / ``call`` / ``check``, which have their own tests.
    """
    seen: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def make(name: str) -> Any:
        def _run(*args: Any, **kwargs: Any) -> int:
            seen.append((name, args, kwargs))
            return EXIT_NOT_CONFORMANT if name == fail_at else EXIT_OK

        return _run

    # Patch the three modules' own ``run``: ``demo`` holds the module objects, and
    # exporting internals like ``ls_cmd`` would trip mypy's no_implicit_reexport.
    monkeypatch.setattr(ls_cmd, "run", make("ls"))
    monkeypatch.setattr(call_cmd, "run", make("call"))
    monkeypatch.setattr(check_cmd, "run", make("check"))
    return seen


class TestLaunchCommand:
    def test_names_the_demo_module(self) -> None:
        assert demo_cmd.server_command() == f"{sys.executable} -m mcpdump.demo"

    def test_argv_is_a_list_not_a_string(self) -> None:
        """An interpreter path with spaces only survives via argv, see the module docstring."""
        assert demo_cmd.options().argv == [sys.executable, "-m", "mcpdump.demo"]

    def test_server_field_is_the_short_display_form(self) -> None:
        """The card shows the short form: the full path would push it to three lines and
        crowd out the capability list. Short for display, long for launch.
        """
        assert demo_cmd.options().server == demo_cmd.DISPLAY_COMMAND
        assert demo_cmd.DISPLAY_COMMAND != demo_cmd.server_command()

    def test_server_stderr_is_off(self) -> None:
        """The bundled server writing to stderr only makes people think something went wrong."""
        assert demo_cmd.options().show_server_stderr is False


class TestCommandMode:
    def test_prints_the_command_and_nothing_else(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--cmd`` output goes to ``$(...)`` or a copy; a single extra character breaks it."""
        assert demo_cmd.run(as_command=True) == EXIT_OK
        assert capsys.readouterr().out.strip() == demo_cmd.server_command()

    def test_does_not_start_a_server(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen = _stub_steps(monkeypatch)
        demo_cmd.run(as_command=True)
        assert seen == []


class TestRun:
    def test_runs_the_three_steps_in_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _quiet(monkeypatch)
        seen = _stub_steps(monkeypatch)
        assert demo_cmd.run() == EXIT_OK
        assert [name for name, _, _ in seen] == ["ls", "call", "check"]

    def test_ls_next_block_is_suppressed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Keeping it would show two Next blocks in a row — step two's title is its content."""
        _quiet(monkeypatch)
        seen = _stub_steps(monkeypatch)
        demo_cmd.run()
        assert seen[0][2]["show_next"] is False

    def test_call_uses_the_demo_tool_and_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _quiet(monkeypatch)
        seen = _stub_steps(monkeypatch)
        demo_cmd.run()
        _, args, kwargs = seen[1]
        assert args[1] == demo_cmd.DEMO_TOOL
        assert kwargs["args"] == demo_cmd.DEMO_ARGS

    def test_stops_at_the_first_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The later steps need the previous session; running on repeats the same error twice."""
        _quiet(monkeypatch)
        seen = _stub_steps(monkeypatch, fail_at="call")
        assert demo_cmd.run() == EXIT_NOT_CONFORMANT
        assert [name for name, _, _ in seen] == ["ls", "call"]

    def test_failure_says_it_is_a_bug_in_mcpdump(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A user seeing a failure will check their own environment; say so up front."""
        stream = _quiet(monkeypatch)
        _stub_steps(monkeypatch, fail_at="check")
        demo_cmd.run()
        assert "bug in mcpdump itself" in stream.getvalue().replace("\n", " ")

    def test_headings_are_numbered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stream = _quiet(monkeypatch)
        _stub_steps(monkeypatch)
        demo_cmd.run()
        text = stream.getvalue()
        for number in ("1. ", "2. ", "3. "):
            assert number in text


class TestEndToEnd:
    def test_a_real_run_succeeds(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Run all three steps for real.

        Slow (three subprocess launches), but the only guard on the first-run experience.
        ``_quiet`` is not used: each step's output goes through its own console, so capsys
        catches what the user actually sees.
        """
        assert demo_cmd.run() == EXIT_OK
        text = capsys.readouterr().out
        assert "Conformance report" in text
        assert "10 checks · 10 passed" in text


class TestRegistration:
    def test_demo_is_the_first_command(self) -> None:
        """The one command a new user can run without knowing what to connect to — show it first."""
        assert [c.name for c in app.registered_commands][0] == "demo"

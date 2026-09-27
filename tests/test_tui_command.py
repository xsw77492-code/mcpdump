"""Tests for the ``mcpdump tui`` shell.

Refusal injects a fake ``Console`` since CI has no terminal; redraw policy reads real
``Live`` attributes rather than a fake; the main loop drives an in-memory session, and
only the end-to-end group spawns a real server subprocess.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from conftest import run_mcpdump
from mcpdump.commands import tui as tui_cmd
from mcpdump.commands.tui import ScreenProblem, make_live, parse_script, run, screen_problem
from mcpdump.core.session import Exchange, ServerInfo
from mcpdump.exits import EXIT_OK, EXIT_USAGE
from mcpdump.runtime import SessionOptions
from mcpdump.ui import (
    Key,
    KeyPress,
    KeyReader,
    PendingCall,
    apply_key,
    initial_state,
)

# ---------------------------------------------------------------- stand-ins


class _FakeSession:
    """An in-memory session stand-in.

    ``exchanges`` returns a new list on every call, matching the real session's
    "re-read the current history" semantics; returning the internal list would make
    ``_drain``'s incremental logic look correct forever.
    """

    def __init__(
        self,
        *,
        tools: list[dict[str, Any]] | None = None,
        exchanges: list[Exchange] | None = None,
        failure: Exception | None = None,
        latency_ms: float = 1.0,
    ) -> None:
        self._tools = [] if tools is None else tools
        self._exchanges = [] if exchanges is None else exchanges
        self._failure = failure
        self._latency_ms = latency_ms
        self.calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def server(self) -> ServerInfo:
        return ServerInfo(name="fake", version="0.1", protocol_version="2025-06-18")

    @property
    def exchanges(self) -> Sequence[Exchange]:
        return list(self._exchanges)

    def list_tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if self._failure is not None:
            raise self._failure
        self._exchanges.append(_exchange("tools/call", elapsed_ms=self._latency_ms))
        return {"content": []}


class _FakeReader:
    """A key-source stand-in, verifying the keyboard opens and always closes outside script mode."""

    def __init__(self, presses: list[KeyPress] | None = None) -> None:
        self._presses: Iterator[KeyPress] = iter(presses or [])
        self.closed = False

    def read(self, timeout: float = 0.1) -> KeyPress | None:
        return next(self._presses, None)

    def close(self) -> None:
        self.closed = True


def _serving(
    session: _FakeSession,
) -> Callable[[SessionOptions], AbstractContextManager[tui_cmd._Session]]:
    """Build a ``session_factory``: whatever arguments arrive, hand back this fake session."""

    def factory(_opts: SessionOptions) -> AbstractContextManager[tui_cmd._Session]:
        return nullcontext(session)

    return factory


# ---------------------------------------------------------------- helpers


def _console(*, width: int = 100, height: int = 26, term: str | None = None) -> Console:
    """A "clean" fake terminal.

    ``force_terminal=True`` is needed to reach the dumb-terminal branch; ``_environ`` is
    passed explicitly so the host's ``TERM`` is not read; ``legacy_windows=False`` keeps
    Rich from deducting a column on Windows.
    """
    environ = {} if term is None else {"TERM": term}
    return Console(
        file=io.StringIO(),
        force_terminal=True,
        width=width,
        height=height,
        legacy_windows=False,
        _environ=environ,
    )


def _exchange(method: str, *, elapsed_ms: float = 1.0, error: str | None = None) -> Exchange:
    return Exchange(
        method=method,
        request_line=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method}),
        response_line=json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
        elapsed_ms=elapsed_ms,
        error=error,
    )


def _tool(
    name: str,
    *,
    required: list[str] | None = None,
    kind: str = "string",
) -> dict[str, Any]:
    """A tool declaration; arguments in ``required`` get a schema generated per ``kind``.

    ``string`` is the default because its skeleton ``{"a": ""}`` puts the cursor inside
    the quotes, so typing one character yields valid JSON; a numeric ``{"a": 0}`` would
    produce invalid JSON like ``9null``.
    """
    names = [] if required is None else required
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {name_: {"type": kind} for name_ in names},
    }
    if required is not None:
        schema["required"] = required
    return {"name": name, "description": f"the {name} tool", "inputSchema": schema}


def _opts() -> SessionOptions:
    return SessionOptions(server="fake")


# ---------------------------------------------------------------- 1. refusal


class TestRefusal:
    def test_a_pipe_is_not_a_terminal(self) -> None:
        """Redirected to a file or pipe, ``is_terminal`` is false and ``Live`` prints only
        the last screen, which is a still picture rather than a UI."""
        assert screen_problem(Console(file=io.StringIO())) is ScreenProblem.NOT_A_TERMINAL

    def test_a_dumb_terminal_is_its_own_problem(self) -> None:
        """With ``TERM=dumb`` Rich hard-codes 80x25 and emits no control codes. Reported
        separately from "not a terminal": the fixes differ."""
        assert screen_problem(_console(term="dumb")) is ScreenProblem.DUMB_TERMINAL

    def test_term_unknown_counts_as_dumb_too(self) -> None:
        assert screen_problem(_console(term="unknown")) is ScreenProblem.DUMB_TERMINAL

    def test_term_is_matched_case_insensitively(self) -> None:
        """Rich lowercases it itself, so our check must follow suit; otherwise on the same
        machine lowercase passes and uppercase is refused."""
        assert screen_problem(_console(term="DUMB")) is ScreenProblem.DUMB_TERMINAL

    def test_a_real_terminal_passes(self) -> None:
        assert screen_problem(_console()) is None

    def test_a_pipe_is_refused_before_the_server_is_touched(self) -> None:
        """The refusal must happen before connecting. Refusing after would spawn a
        subprocess and report "server did not respond" instead of the real cause."""

        def exploding(_opts: SessionOptions) -> AbstractContextManager[tui_cmd._Session]:
            raise AssertionError("it must not connect to the server before refusing")

        code = run(_opts(), console=Console(file=io.StringIO()), session_factory=exploding)
        assert code == EXIT_USAGE

    def test_the_refusal_names_a_working_alternative(self, capsys: Any) -> None:
        """With only "a terminal is required" CI users go to the docs; give a copyable command."""
        code = run(
            _opts(),
            console=Console(file=io.StringIO()),
            session_factory=_serving(_FakeSession()),
        )
        assert code == EXIT_USAGE
        assert "mcpdump ls" in capsys.readouterr().err

    def test_the_dumb_terminal_refusal_quotes_the_term(self, capsys: Any) -> None:
        """Echo the ``TERM`` value verbatim so the user knows which variable to change."""
        run(_opts(), console=_console(term="dumb"), session_factory=_serving(_FakeSession()))
        assert "dumb" in capsys.readouterr().err


# ---------------------------------------------------------------- 2. redraw policy


class TestRedrawPolicy:
    def _live(self) -> Live:
        return make_live(Group(Text("x")), _console())

    def test_the_screen_is_not_taken_over(self) -> None:
        """``screen=True`` redraws the whole screen each frame, which is where the flicker
        comes from; ``False`` rewrites only the changed region. ``Live`` stores the flag on
        ``_screen``, the only switch for a full-screen redraw.
        """
        assert self._live()._screen is False

    def test_nothing_refreshes_on_a_timer(self) -> None:
        """Redraws follow state changes, not a background thread repainting the same thing."""
        assert self._live().auto_refresh is False

    def test_the_last_frame_stays_in_history(self) -> None:
        """``transient=True`` erases the picture on exit; keeping it lets the user scroll
        back to the last screen as evidence."""
        assert self._live().transient is False

    def test_overflow_crops_instead_of_ellipsising(self) -> None:
        """The default ``ellipsis`` replaces overflowing lines with a single ``...`` line;
        cropping is more predictable, since the cropped rows were extra anyway."""
        assert self._live().vertical_overflow == "crop"

    def test_the_console_is_the_one_we_handed_it(self) -> None:
        """Geometry is read from the console the ``Live`` uses; swap the console and it follows."""
        console = _console()
        assert make_live(Group(Text("x")), console).console is console

    def test_the_view_is_wrapped_so_rich_can_render_it(self) -> None:
        """``build_view`` returns ``list[Text]``, and ``list`` has no ``__rich_console__``;
        handing it straight to Rich degrades to ``str(list)`` and prints a repr on screen.
        """
        stream = io.StringIO()
        console = Console(file=stream, width=40, height=10, legacy_windows=False)
        console.print(tui_cmd._renderable(initial_state(), width=40, height=6))
        out = stream.getvalue()
        assert "<text" not in out
        assert "[" not in out.splitlines()[0]


# ---------------------------------------------------------------- 3. script parsing


class TestParseScript:
    def test_a_single_character_is_a_character_key(self) -> None:
        assert parse_script("q") == [KeyPress(Key.CHAR, "q")]

    def test_named_keys_are_recognised(self) -> None:
        assert parse_script("tab,enter,esc") == [
            KeyPress(Key.TAB, ""),
            KeyPress(Key.ENTER, ""),
            KeyPress(Key.ESC, ""),
        ]

    def test_names_are_case_insensitive(self) -> None:
        """Case must not trip users up — ``TAB`` and ``tab`` are the same key."""
        assert parse_script("TAB") == parse_script("tab")

    def test_whitespace_around_tokens_is_ignored(self) -> None:
        """A space after the comma is the most natural way to write it."""
        assert parse_script("tab, enter , q") == parse_script("tab,enter,q")

    def test_empty_tokens_are_skipped(self) -> None:
        """The empty segment in ``a,,q`` is skipped, not reported — it is a typo, not an error."""
        assert parse_script("a,,q") == parse_script("a,q")

    def test_an_empty_script_is_empty(self) -> None:
        assert parse_script("") == []

    def test_space_is_spelled_out(self) -> None:
        """A space character cannot be written into a comma-separated list, so it needs a name."""
        presses = parse_script("space")
        assert presses == [KeyPress(Key.CHAR, " ")]
        assert presses[0].is_printable

    def test_an_unknown_name_is_an_error_not_a_skip(self) -> None:
        """Skipping silently yields "passes, one press short" — harder to trace than a failure."""
        try:
            parse_script("tab,nope,q")
        except ValueError as exc:
            assert str(exc) == "nope"
        else:
            raise AssertionError("an unrecognised key name must raise")

    def test_the_error_names_the_offending_token(self, capsys: Any) -> None:
        """The error must name the offending token; otherwise a long script cannot be located."""
        code = run(
            _opts(),
            script="tab,nope,q",
            console=_console(),
            session_factory=_serving(_FakeSession()),
        )
        assert code == EXIT_USAGE
        assert "nope" in capsys.readouterr().err

    def test_the_error_lists_the_known_names(self, capsys: Any) -> None:
        run(_opts(), script="nope", console=_console(), session_factory=_serving(_FakeSession()))
        err = capsys.readouterr().err
        assert "pageup" in err
        assert "backspace" in err


# ---------------------------------------------------------------- 4. key sources


class TestKeySources:
    def test_scripted_keys_satisfy_the_reader_protocol(self) -> None:
        """``KeyReader`` is a ``runtime_checkable`` Protocol, so this assertion is a real
        structural check, not "it looks like one"."""
        assert isinstance(tui_cmd._ScriptedKeys([]), KeyReader)

    def test_scripted_keys_run_out(self) -> None:
        keys = tui_cmd._ScriptedKeys([KeyPress(Key.CHAR, "q")])
        assert keys.read() == KeyPress(Key.CHAR, "q")
        assert keys.read() is None

    def test_scripted_keys_do_not_touch_a_terminal(self) -> None:
        """Script mode never opens a terminal, so it runs the same on every platform."""
        tui_cmd._ScriptedKeys([]).close()

    def test_script_mode_never_opens_the_keyboard(self) -> None:
        """The whole point of ``--script`` is "no keyboard"; opening one really does hang CI."""

        def exploding() -> KeyReader:
            raise AssertionError("script mode must not touch the keyboard")

        code = run(
            _opts(),
            script="q",
            console=_console(),
            reader_factory=exploding,
            session_factory=_serving(_FakeSession()),
        )
        assert code == EXIT_OK

    def test_the_keyboard_is_closed_even_when_the_loop_ends(self) -> None:
        """Without closing it the terminal stays in raw mode and the user cannot see their input."""
        reader = _FakeReader([KeyPress(Key.CHAR, "q")])
        run(
            _opts(),
            console=_console(),
            reader_factory=lambda: reader,
            session_factory=_serving(_FakeSession()),
        )
        assert reader.closed

    def test_the_keyboard_drives_the_loop_when_there_is_no_script(self) -> None:
        """In non-script mode keys really are read — ``enter`` reached the tool."""
        reader = _FakeReader([KeyPress(Key.ENTER, ""), KeyPress(Key.CHAR, "q")])
        session = _FakeSession(tools=[_tool("ping")])
        run(
            _opts(),
            console=_console(),
            reader_factory=lambda: reader,
            session_factory=_serving(session),
        )
        assert session.calls == [("ping", {})]


# ---------------------------------------------------------------- 5. main loop


class TestLoop:
    def test_a_script_runs_to_the_end(self) -> None:
        code = run(
            _opts(),
            script="tab,esc,q",
            console=_console(),
            session_factory=_serving(_FakeSession(tools=[_tool("add")])),
        )
        assert code == EXIT_OK

    def test_script_mode_skips_the_terminal_check(self) -> None:
        """The entire reason ``--script`` exists is that it runs where there is no terminal."""
        pipe = Console(file=io.StringIO())
        assert screen_problem(pipe) is ScreenProblem.NOT_A_TERMINAL
        code = run(
            _opts(),
            script="q",
            console=pipe,
            session_factory=_serving(_FakeSession()),
        )
        assert code == EXIT_OK

    def test_a_non_terminal_run_prints_only_the_final_frame(self) -> None:
        """On a non-TTY, ``Live`` writes one screen only on exit, so CI logs are "one
        screen + exit code" with no intermediate frames — this is what makes ``--script``
        usable as a smoke test."""
        stream = io.StringIO()
        console = Console(
            file=stream,
            force_terminal=False,
            width=79,
            height=25,
            legacy_windows=False,
            _environ={},
        )
        code = run(
            _opts(),
            script="tab,esc,q",
            console=console,
            session_factory=_serving(_FakeSession(tools=[_tool("add")])),
        )
        assert code == EXIT_OK
        assert stream.getvalue().count("┌") == 1

    def test_the_server_stderr_is_switched_off(self) -> None:
        """Server stderr competes with ``Live`` for the same screen. The command layer
        turns it off itself, without relying on the CLI passing the right flag."""
        seen: list[SessionOptions] = []

        def factory(opts: SessionOptions) -> AbstractContextManager[tui_cmd._Session]:
            seen.append(opts)
            return nullcontext(_FakeSession())

        run(_opts(), script="q", console=_console(), session_factory=factory)
        assert [opts.show_server_stderr for opts in seen] == [False]

    def test_enter_calls_a_tool_that_needs_no_arguments(self) -> None:
        """With no required arguments the args line is a pointless step — enter should call it."""
        session = _FakeSession(tools=[_tool("ping")])
        run(_opts(), script="enter,q", console=_console(), session_factory=_serving(session))
        assert session.calls == [("ping", {})]

    def test_a_failing_call_does_not_take_the_screen_down(self) -> None:
        """One failed call must not take the whole UI down — the user likely wants the next tool."""
        session = _FakeSession(tools=[_tool("boom")], failure=TimeoutError("timed out"))
        code = run(
            _opts(),
            script="enter,tab,q",
            console=_console(),
            session_factory=_serving(session),
        )
        assert code == EXIT_OK
        assert len(session.calls) == 1

    def test_a_failing_call_is_recorded_in_the_timeline(self) -> None:
        """Failures also enter the timeline, with ``ok=False`` — else a slow call is invisible."""
        session = _FakeSession(failure=TimeoutError("timed out"))
        state = tui_cmd._call(
            initial_state(), session, PendingCall("boom", {})
        )
        assert [record.ok for record in state.calls] == [False]
        assert state.calls[0].method == "boom"
        assert state.busy is False
        assert "timed out" in state.error_text

    def test_a_successful_call_is_recorded_as_ok(self) -> None:
        session = _FakeSession()
        state = tui_cmd._call(
            initial_state(), session, PendingCall("add", {"a": 1})
        )
        assert [record.ok for record in state.calls] == [True]
        assert session.calls == [("add", {"a": 1})]
        assert state.error_text == ""

    def test_the_elapsed_time_is_wall_clock_not_protocol_time(self) -> None:
        """The timing measures wall clock, not the protocol round trip: the session reports
        a 999999ms round trip, but the number on screen must be the one measured here."""
        session = _FakeSession(latency_ms=999_999.0)
        state = tui_cmd._call(
            initial_state(), session, PendingCall("add", {})
        )
        assert state.calls[0].elapsed_ms < 1000.0

    def test_enter_on_a_tool_with_required_args_opens_the_args_line(self) -> None:
        """With required arguments it enters args mode and sends no request; a request
        missing arguments should never go out."""
        session = _FakeSession(tools=[_tool("add", required=["a", "b"])])
        run(_opts(), script="enter,esc,q", console=_console(), session_factory=_serving(session))
        assert session.calls == []

    def test_typing_into_the_args_line_reaches_the_server(self) -> None:
        """The skeleton value is a placeholder. Once the user edits it and presses enter,
        the edited value must be what goes out."""
        session = _FakeSession(tools=[_tool("add", required=["a"])])
        run(
            _opts(),
            script="enter,9,enter,q",
            console=_console(),
            session_factory=_serving(session),
        )
        assert session.calls == [("add", {"a": "9"})]

    def test_an_unknown_key_leaves_the_state_alone(self) -> None:
        """``apply_key`` returns the same state object for a key it does not recognise. The
        shell's redraw predicate relies on this identity; once it stops holding, the UI
        repaints continuously as the user mashes keys."""
        state = initial_state()
        assert apply_key(state, KeyPress(Key.UNKNOWN, "")).state is state

    def test_quit_wins_over_everything(self) -> None:
        session = _FakeSession(tools=[_tool("ping")])
        run(_opts(), script="q", console=_console(), session_factory=_serving(session))
        assert session.calls == []


# ---------------------------------------------------------------- 6. wire drain


class TestWireDrain:
    def test_frames_are_drained_incrementally(self) -> None:
        """``seen`` is how many rows have already been moved over; re-counting
        ``len(exchanges)`` would under- or re-move rows when the session trims its history
        internally, and neither failure raises."""
        session = _FakeSession(exchanges=[_exchange("initialize")])
        state, seen = tui_cmd._drain(initial_state(), session, 0)
        first = len(state.frames)
        assert seen == 1

        session._exchanges.append(_exchange("tools/list"))
        state, seen = tui_cmd._drain(state, session, seen)
        assert seen == 2
        assert len(state.frames) > first

    def test_draining_twice_changes_nothing(self) -> None:
        session = _FakeSession(exchanges=[_exchange("initialize")])
        state, seen = tui_cmd._drain(initial_state(), session, 0)
        again, _ = tui_cmd._drain(state, session, seen)
        assert len(again.frames) == len(state.frames)

    def test_each_exchange_becomes_a_request_and_a_response(self) -> None:
        """One round trip is two frames: a request frame and a response frame. Drawing only
        one leaves the user unable to tell whether it went out or came back."""
        session = _FakeSession(exchanges=[_exchange("initialize")])
        state, _ = tui_cmd._drain(initial_state(), session, 0)
        assert len(state.frames) == 2

    def test_a_call_adds_its_round_trip_to_the_wire(self) -> None:
        session = _FakeSession()
        state = tui_cmd._call(
            initial_state(), session, PendingCall("ping", {})
        )
        state, seen = tui_cmd._drain(state, session, 0)
        assert seen == 1
        assert len(state.frames) == 2

    def test_the_tool_list_is_adopted_once_at_handshake(self) -> None:
        """The tool list is fetched in full after the handshake; calling ``list_tools``
        twice means someone re-fetches inside the loop and resets the user's selection."""
        calls: list[str] = []

        class _Counting(_FakeSession):
            def list_tools(self) -> list[dict[str, Any]]:
                calls.append("list")
                return super().list_tools()

        run(
            _opts(),
            script="tab,esc,q",
            console=_console(),
            session_factory=_serving(_Counting(tools=[_tool("a"), _tool("b")])),
        )
        assert calls == ["list"]


# ---------------------------------------------------------------- 7. end to end


class TestEndToEnd:
    """Only this group spawns a subprocess — it verifies "really connected to a real server"."""

    def test_the_script_mode_connects_to_a_real_server(
        self, echo_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        result = run_mcpdump(["tui", echo_server_cmd, "--script", "q"], mcpdump_env)
        assert result.returncode == EXIT_OK, result.stderr

    def test_pane_switching_runs_against_a_real_server(
        self, echo_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        """Press a few more keys to verify the state machine does not dead-end on a real session."""
        result = run_mcpdump(["tui", echo_server_cmd, "--script", "tab,tab,esc,q"], mcpdump_env)
        assert result.returncode == EXIT_OK, result.stderr

    def test_calling_a_tool_runs_against_a_real_server(
        self, tools_only_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        """Call a tool on a real server: walks the whole "key → request → wire → timeline" path."""
        result = run_mcpdump(["tui", tools_only_server_cmd, "--script", "enter,q"], mcpdump_env)
        assert result.returncode == EXIT_OK, result.stderr

    def test_a_bad_key_name_fails_before_connecting(self, mcpdump_env: dict[str, str]) -> None:
        """A bad script must not connect — connecting first misdirects the failure message."""
        result = run_mcpdump(["tui", "no-such-server-binary", "--script", "nope"], mcpdump_env)
        assert result.returncode == EXIT_USAGE
        assert "nope" in result.stderr

    def test_the_hidden_option_is_not_advertised(
        self, echo_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        """``--script`` is an internal switch for CI and screen recording; keep it out of help."""
        result = run_mcpdump(["tui", "--help"], mcpdump_env)
        assert result.returncode == EXIT_OK
        assert "--script" not in result.stdout

    def test_the_command_is_registered(self, mcpdump_env: dict[str, str]) -> None:
        result = run_mcpdump(["--help"], mcpdump_env)
        assert result.returncode == EXIT_OK
        assert "tui" in result.stdout

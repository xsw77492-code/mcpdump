"""``mcpdump tui`` -- the interactive four-pane interface.

This module is the **only** place that touches the terminal: the state machine
(``ui.tui_state``) is pure, the view (``ui.tui_view``) is pure, and the keyboard
(``ui.keys``) only decodes bytes into key presses. Keeping "when to redraw, when to
issue a request" here is what lets the other three be verified offline.

## Three deliberate decisions

**1. Refuse rather than degrade.** A non-TTY or a dumb terminal exits early instead
of drawing a partial interface. On a dumb terminal Rich **hardcodes the size to
80x25** (the second branch of ``Console.size``), unrelated to the real terminal, and
it emits no control codes, so ``Live`` degenerates to "print the last screen". Both
together mean the four-pane layout does not exist there -- a frozen fake interface
is worse than a refusal.

**2. Calls are synchronous.** The interface freezes while a request is in flight
(``busy``). Moving the call to a thread and keeping the UI responsive would open an
indeterminate window between "the state the UI shows" and "the state the server is
in": the user thinks ESC cancelled it while the request is already on the wire. In a
troubleshooting tool, what the UI says must match what actually happens.

**3. Redraws are driven by state changes, not by a clock.** ``apply_key`` returns
**the original state object** for keys it does not recognize, so an ``is``
comparison is exactly "nothing changed this round". A timed redraw would rewrite the
same region while the user is idle, which shows up as flicker on some terminals --
and "no full-screen flicker" is the acceptance criterion for this feature.

## Three empirically established facts; read before changing

- ``Live.update``'s ``refresh`` defaults to ``False`` and must be passed ``True``
  explicitly, or the screen never refreshes (``Live``'s ``auto_refresh`` is also off,
  see ``make_live``).
- ``build_view`` returns ``list[Text]``, which **cannot be handed to ``Live``
  directly**: ``list`` has no ``__rich_console__``, so Rich falls back to
  ``str(list)`` and the screen shows a repr like
  ``[<text 'hello' [] ''>, ...]``. It must be wrapped in a ``Group``.
- ``Console.size``'s width **already subtracts the ``legacy_windows`` column**,
  so component widths must come from ``console.size.width``; using
  ``console.width`` is one column too wide on Windows and puts the border exactly
  on the wrap boundary.

## Non-TTY degradation happens to be what CI wants

``Live.refresh`` only really outputs when ``not self._started`` (the third branch),
and ``_started`` stays true for the whole loop -- so **not a single frame of the
interaction is printed**. On exit ``stop()`` clears ``_started`` and refreshes once,
printing one final screen through the render hook. Measured: ``--script "tab,esc,q"``
piped through produces exactly **one screen, exit code 0, empty stderr**, with no
intermediate output. That is why ``--script`` works as a CI smoke test.

## The server's stderr must be off during the TUI

``Live`` redraws in place on stdout while the server's stderr is relayed to
mcpdump's stderr by default. Sharing one screen interleaves them and shreds the
display, so ``show_server_stderr=False`` is forced here. To read those logs, use
``mcpdump watch`` or ``mcpdump call`` -- that is what they exist for. ``--trace`` is
excluded for the same reason: the wire pane replaces it.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Protocol

from rich.console import Console, Group
from rich.live import Live

from ..core.jsonrpc import JsonRpcError
from ..core.session import Exchange, ServerInfo
from ..exits import EXIT_ENVIRONMENT, EXIT_OK, EXIT_PROTOCOL, EXIT_TIMEOUT, EXIT_USAGE
from ..i18n import t
from ..runtime import SessionOptions, open_session
from ..ui import (
    CallRecord,
    Key,
    KeyPress,
    KeyReader,
    PendingCall,
    ToolEntry,
    TuiAction,
    TuiState,
    apply_key,
    build_view,
    err_console,
    frames_of,
    initial_state,
    push_call,
    push_frame,
    render_error,
    select_key_reader,
    set_server,
    set_tools,
    settle,
    styled_lines,
    theme,
)

__all__ = [
    "ScreenProblem",
    "make_live",
    "parse_script",
    "run",
    "screen_problem",
]

#: How long one key read waits when idle. **It only decides how often a terminal
#: resize is noticed** -- redraws are state-driven, so raising it does not make keys
#: feel laggy and lowering it does not make them feel snappier.
_POLL_SECONDS = 0.1

#: Key names usable in ``--script``. **Compared case-insensitively** -- the user
#: should not be tripped up by case. ``space`` is listed separately because "a single
#: space character" cannot be written into a comma-separated list.
_NAMED_KEYS: dict[str, Key] = {
    "backspace": Key.BACKSPACE,
    "delete": Key.DELETE,
    "down": Key.DOWN,
    "end": Key.END,
    "enter": Key.ENTER,
    "esc": Key.ESC,
    "home": Key.HOME,
    "left": Key.LEFT,
    "pagedown": Key.PAGE_DOWN,
    "pageup": Key.PAGE_UP,
    "right": Key.RIGHT,
    "space": Key.CHAR,
    "tab": Key.TAB,
    "up": Key.UP,
}


class ScreenProblem(str, Enum):
    """The two ways a terminal cannot support the four-pane interface. **Listed
    separately because the next step differs for each.**"""

    NOT_A_TERMINAL = "not_a_terminal"
    DUMB_TERMINAL = "dumb_terminal"


class _Session(Protocol):
    """The small slice of ``MCPSession`` that ``_interact`` actually uses.

    **Not depending on ``MCPSession`` directly**: that would leave the
    "key -> view -> request" chain verifiable only by starting a real child process,
    and process scheduling is out of scope for these tests -- whether one failed call
    should take down the whole interface has nothing to do with the server being a
    child process.
    """

    @property
    def server(self) -> ServerInfo: ...

    @property
    def exchanges(self) -> Sequence[Exchange]: ...

    def list_tools(self) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class _Keys:
    """The key source. ``finite`` true means "no more input means finished", false
    means "wait another round".

    Keeping those two meanings apart leaves a single ``None`` check in the main loop
    -- and conflating "timed out" with "finished" is exactly how interactive
    interfaces deadlock.
    """

    reader: KeyReader
    finite: bool


class _ScriptedKeys:
    """Use a list of key presses as a keyboard.

    **Imports no platform modules**: script mode never opens a terminal, so it
    produces identical results on Windows and in CI -- which is why ``--script``
    exists.
    """

    def __init__(self, presses: list[KeyPress]) -> None:
        self._presses = iter(presses)

    def read(self, timeout: float = 0.1) -> KeyPress | None:
        return next(self._presses, None)

    def close(self) -> None:
        """Nothing to release. Present so this class matches the ``KeyReader`` shape."""


class _Screen:
    """Bundle "draw one screen" into an object, because it must remember the last one.

    The redraw test is ``state is not self._state``: ``apply_key`` returns **the
    original state object** for keys it does not recognize, so an ``is`` comparison
    is exactly "nothing changed this round". The terminal size is recorded alongside
    -- a resize must redraw even when the state is unchanged.
    """

    def __init__(self, live: Live, console: Console) -> None:
        self._live = live
        self._console = console
        self._state: TuiState | None = None
        self._size: tuple[int, int] | None = None

    def draw(self, state: TuiState, *, force: bool = False) -> None:
        size = _size_of(self._console)
        if not force and state is self._state and size == self._size:
            return
        self._live.update(_renderable(state, width=size[0], height=size[1]), refresh=True)
        self._state = state
        self._size = size


# ---------------------------------------------------------------- pure functions


def screen_problem(console: Console) -> ScreenProblem | None:
    """Whether this terminal can draw the four-pane interface. ``None`` means it can.

    The criteria come from Rich's measured behaviour, not guesswork: with
    ``is_terminal`` false, ``Live`` emits no control codes and merely prints the last
    screen; with ``is_dumb_terminal`` true, ``Console.size`` returns ``(80, 25)``
    outright (the second branch of the ``size`` property in ``rich/console.py``),
    unrelated to the real terminal. Either one turns the four-pane layout into an
    interface that **looks fine and is entirely wrong**.
    """
    if not console.is_terminal:
        return ScreenProblem.NOT_A_TERMINAL
    if console.is_dumb_terminal:
        return ScreenProblem.DUMB_TERMINAL
    return None


def make_live(initial: Group, console: Console) -> Live:
    """Build the ``Live`` used by the TUI. **All redraw policy lives in this one
    function.**

    Each of the three arguments has a cost; read this before changing them:

    - ``screen=False``: ``LiveRender``'s ``reset`` is ``Control.home()`` when
      ``screen=True`` -- a full-screen repaint every frame, which is where flicker
      comes from; with ``False`` it is ``position_cursor()``: move up N lines and
      erase line by line, **rewriting only the changed region**. The cost is sharing
      a screen with shell history; the benefit is that the final screen stays in
      history and can be scrolled back as evidence.
    - ``auto_refresh=False``: redraw timing belongs to the main loop. A background
      refresh thread repaints on a fixed period, and during the periods where the
      user does nothing it draws identical content -- pure waste, and on some
      terminals visible flicker.
    - ``vertical_overflow="crop"``: the default ``"ellipsis"`` truncates to
      ``height - 1`` lines and appends a ``...`` line when content exceeds the
      terminal height. ``build_view`` already guarantees the line count equals the
      terminal height exactly, so normally neither triggers; but shrinking the
      terminal between two size reads does trigger it, and cropping is more
      predictable than swapping in an ellipsis -- the dropped lines were computed for
      the old, larger size anyway.
    """
    return Live(
        initial,
        console=console,
        screen=False,
        auto_refresh=False,
        transient=False,
        vertical_overflow="crop",
    )


def _renderable(state: TuiState, *, width: int, height: int) -> Group:
    """``build_view``'s lines -> a Rich renderable.

    **This step cannot be skipped**: ``list`` has no ``__rich_console__``, so handing
    it to Rich directly falls back to ``str(list)`` and the screen shows a repr like
    ``[<text 'hello' [] ''>, ...]`` (measured). ``Group`` is the correct expression of
    "render line by line". Both the view and the shell go through this one exit, so
    nobody can forget to wrap on some path.
    """
    return Group(*build_view(state, width=width, height=height))


def parse_script(text: str) -> list[KeyPress]:
    """Split ``--script``'s string into key presses.

    Comma-separated rather than space-separated: argument lines often contain values
    with spaces, such as ``{"city": "Paris"}``, and splitting on spaces would require
    another layer of escaping rules -- the one thing a user can never infer from the
    help text. A literal space character is written ``space``.

    Unknown names **raise rather than being skipped**: a typo in a recording script
    would otherwise produce a "passed, but one key press short" result, which is far
    harder to trace than a plain failure.
    """
    presses: list[KeyPress] = []
    for raw in text.split(","):
        token = raw.strip()
        if not token:
            continue
        named = _NAMED_KEYS.get(token.lower())
        if named is not None:
            presses.append(KeyPress(named, " " if named is Key.CHAR else ""))
        elif len(token) == 1:
            presses.append(KeyPress(Key.CHAR, token))
        else:
            raise ValueError(token)
    return presses


# ---------------------------------------------------------------- shell


def run(
    opts: SessionOptions,
    *,
    script: str | None = None,
    console: Console | None = None,
    reader_factory: Callable[[], KeyReader] | None = None,
    session_factory: Callable[[SessionOptions], AbstractContextManager[_Session]] = open_session,
) -> int:
    """Run one interactive session and return the exit code.

    ``console`` / ``reader_factory`` / ``session_factory`` are seams for tests: a real
    terminal cannot exist in CI, and the "non-TTY must be refused" rule can only be
    verified by injecting a non-TTY ``Console``.
    """
    screen = theme.console if console is None else console

    scripted: list[KeyPress] | None = None
    if script is not None:
        try:
            scripted = parse_script(script)
        except ValueError as exc:
            err_console.print(styled_lines([
                (t("tui.script.unknown_key", token=str(exc)), "mcpdump.err"),
                (t("tui.script.known_keys", names=", ".join(sorted(_NAMED_KEYS))), "mcpdump.dim"),
            ]))
            return EXIT_USAGE

    # --script is for CI, where stdout is not a terminal -- so script mode skips this
    # check. Without that exception, --script could only run on machines with a
    # terminal, which would defeat its purpose.
    if scripted is None:
        problem = screen_problem(screen)
        if problem is not None:
            _refuse(problem, term=os.environ.get("TERM", ""))
            return EXIT_USAGE

    # The server's stderr competes with Live for the same screen, so it is forced off
    # for the duration. See the module docstring.
    opts = replace(opts, show_server_stderr=False)

    reader: KeyReader | None = None
    keys: _Keys
    if scripted is not None:
        keys = _Keys(_ScriptedKeys(scripted), finite=True)
    else:
        reader = (reader_factory or select_key_reader)()
        keys = _Keys(reader, finite=False)

    try:
        return _interact(opts, screen=screen, keys=keys, session_factory=session_factory)
    except JsonRpcError as exc:
        render_error(err_console, exc)
        return EXIT_PROTOCOL
    except TimeoutError as exc:
        render_error(err_console, exc)
        return EXIT_TIMEOUT
    except RuntimeError as exc:
        render_error(err_console, exc)
        return EXIT_ENVIRONMENT
    finally:
        if reader is not None:
            reader.close()


def _refuse(problem: ScreenProblem, *, term: str) -> None:
    """Refuse to start, and give the next step.

    **A runnable alternative is mandatory.** Saying only "an interactive terminal is
    required" leaves the user (especially in CI) digging through docs; giving
    ``mcpdump ls <SERVER>`` lets them get what they wanted immediately.
    """
    if problem is ScreenProblem.DUMB_TERMINAL:
        lines = [
            (t("tui.dumb_terminal", term=term or "dumb"), "mcpdump.err"),
            (t("tui.dumb_terminal.hint"), "mcpdump.dim"),
        ]
    else:
        lines = [
            (t("tui.need_terminal"), "mcpdump.err"),
            (t("tui.need_terminal.hint"), "mcpdump.dim"),
        ]
    err_console.print(styled_lines(lines))


def _interact(
    opts: SessionOptions,
    *,
    screen: Console,
    keys: _Keys,
    session_factory: Callable[[SessionOptions], AbstractContextManager[_Session]],
) -> int:
    """Assemble the view and the session, then run the main loop.

    ``Live`` starts before the handshake: the handshake can take up to one
    ``--timeout``, and during that time the screen must show "connecting..." rather
    than a blank -- a user seeing nothing assumes the program failed to start.
    """
    state = initial_state(transport=opts.describe_transport())
    width, height = _size_of(screen)
    with make_live(_renderable(state, width=width, height=height), screen) as live:
        view = _Screen(live, screen)
        view.draw(state, force=True)
        with session_factory(opts) as session:
            state = _adopt(state, session, opts)
            state, seen = _drain(state, session, 0)
            view.draw(state, force=True)
            return _loop(view, state, session, seen, keys=keys)


def _adopt(state: TuiState, session: _Session, opts: SessionOptions) -> TuiState:
    """Move the freshly handshaken session into the view: server identity + tool list.

    The tool list is fetched **in one go**, not paginated: ``tools/list`` usually
    returns a few dozen entries, and pagination would turn filtering, selection, and
    invocation into three paths that can each be "not loaded yet".
    """
    tools = tuple(ToolEntry.from_payload(payload) for payload in session.list_tools())
    return set_tools(set_server(state, session.server, opts.describe_transport()), tools)


def _drain(state: TuiState, session: _Session, seen: int) -> tuple[TuiState, int]:
    """Move newly produced frames into the view.

    ``seen`` is **the number already moved**, not a fresh ``len(session.exchanges)``
    each round: if the session ever trims its history, a recounted length would move
    too few or too many -- and neither reports an error.
    """
    fresh = session.exchanges[seen:]
    for frame in frames_of(fresh):
        state = push_frame(state, frame)
    return state, seen + len(fresh)


def _loop(
    view: _Screen,
    state: TuiState,
    session: _Session,
    seen: int,
    *,
    keys: _Keys,
) -> int:
    while True:
        press = keys.reader.read(timeout=_POLL_SECONDS)
        if press is None:
            if keys.finite:
                return EXIT_OK
            # Timeout: the terminal may also have been resized. Whether to redraw is
            # draw's call.
            view.draw(state)
            continue

        step = apply_key(state, press)
        state = step.state

        if step.action is TuiAction.QUIT:
            view.draw(state, force=True)
            return EXIT_OK

        if step.action is TuiAction.CALL_TOOL and step.call is not None:
            # Put "running..." on screen before sending the request: otherwise the
            # view stays on the pre-keypress frame for the whole call and the user
            # cannot tell whether anything happened.
            view.draw(state, force=True)
            state = _call(state, session, step.call)
            state, seen = _drain(state, session, seen)

        view.draw(state)


def _call(state: TuiState, session: _Session, call: PendingCall) -> TuiState:
    """Run one tool call synchronously and record the result in the timeline.

    Elapsed time is **wall clock** from ``perf_counter``, not
    ``Exchange.elapsed_ms``: the latter covers only the protocol round trip, whereas
    the user waits from pressing enter until the screen moves again, including local
    serialization and child-process scheduling. A number on screen shorter than the
    real wait is a lie.

    Exceptions are folded into a failed timeline entry here rather than propagated: a
    failed call should not take the whole interface down -- the user most likely wants
    to call the next tool.
    """
    started = time.perf_counter()
    try:
        session.call_tool(call.tool, call.arguments)
    except (JsonRpcError, TimeoutError, RuntimeError, OSError) as exc:
        record = CallRecord(call.tool, _elapsed_ms(started), False, str(exc))
        return settle(push_call(state, record), str(exc))
    return settle(push_call(state, CallRecord(call.tool, _elapsed_ms(started), True)))


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _size_of(console: Console) -> tuple[int, int]:
    """The current terminal size.

    **Use ``console.size``, not ``console.width``**: the former already subtracts the
    ``legacy_windows`` column and the latter does not. Drawing to ``width`` on Windows
    puts the right border exactly on the wrap boundary, so every line wraps into an
    extra blank line.
    """
    size = console.size
    return size.width, size.height

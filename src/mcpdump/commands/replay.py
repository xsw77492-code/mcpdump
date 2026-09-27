"""``mcpdump replay`` -- replay a session recording without launching anything.

One purpose: **let someone else reproduce your problem**. The recording attaches
to an issue; the other side needs no Node, no API key, no matching OS.

Its output must therefore be a function of the file alone. Anything that depends
on "now" -- recomputing durations, probing the terminal, reading environment
variables -- would make one command produce different results on two machines,
destroying the only thing it is for.
"""

from __future__ import annotations

import json
from typing import Any

from ..exits import EXIT_OK, EXIT_USAGE
from ..i18n import t
from ..services.recorder import RecordError, iter_frames, load_header
from ..services.replayer import build_result, to_exchanges
from ..services.session_report import render_replay
from ..ui import console, err_console, hint_lines, render_next_steps, styled_lines

#: Default cap for ``--limit``. Dumping ten thousand frames to a terminal is
#: both useless and slow, and anyone needing all of them writes ``--limit 0``
#: (0 means unlimited).
DEFAULT_LIMIT = 200


def run(
    path: str,
    *,
    as_json: bool = False,
    step: bool = False,
    limit: int = DEFAULT_LIMIT,
    show_wire: bool = False,
) -> int:
    try:
        header = load_header(path)
        frames = iter_frames(path)
        if as_json:
            # Streaming read, single serialization: --json consumers want everything.
            result = build_result(list(frames))
            payload: dict[str, Any] = {
                "source": path,
                "format": {
                    "name": "mcpdump-session",
                    "version": header.version,
                    "recordedAt": header.at,
                },
                "argv": list(header.argv),
                **result.to_dict(),
            }
            # --json is machine-read, so it must use plain print, not Rich.
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return EXIT_OK

        result = build_result(list(frames))
    except RecordError as exc:
        err_console.print(styled_lines([(str(exc), "mcpdump.err")]))
        return EXIT_USAGE

    # ``--limit 0`` means unlimited. An explicit sentinel rather than a negative
    # number, since ``-1`` on the command line would be parsed as an option.
    cap = None if limit == 0 else limit
    render_replay(console, result, source=path, limit=cap, step_mode=step)

    if show_wire and result.steps:
        # ``--wire`` is for reading raw frames: it reuses ``ui.wire``'s existing
        # layout, keeping the visual language consistent with ``ls`` / ``call``.
        from ..ui import render_wire

        render_wire(console, to_exchanges(result.steps), full=True, summary=False)

    render_next_steps(
        console,
        hint_lines([
            t("replay.next.diff", path=path),
            t("replay.next.json", path=path),
        ]),
        title=t("replay.next_steps"),
    )
    return EXIT_OK

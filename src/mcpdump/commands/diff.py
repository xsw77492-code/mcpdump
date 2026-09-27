"""``mcpdump diff`` -- structural differences between two session recordings.

Answers one question: **did behaviour change after my edit?**

Not a text diff. Recordings carry ``seq`` / ``atMs`` / ``elapsedMs``, so a
line-by-line comparison reports the whole file as changed and buries the real
change. This compares contracts instead: tools added or removed, argument schema
changes, capability changes, latency regressions. The rules live in
``services/diff.py``.
"""

from __future__ import annotations

import json
from typing import Any

from ..exits import EXIT_NOT_CONFORMANT, EXIT_OK, EXIT_USAGE
from ..i18n import t
from ..services.diff import build_contract, diff_contracts
from ..services.recorder import RecordError, iter_frames
from ..services.session_report import render_diff
from ..ui import console, err_console, hint_lines, render_next_steps, styled_lines


def run(before: str, after: str, *, as_json: bool = False) -> int:
    """Compare two recordings.

    Exit code: ``0`` no differences, ``2`` differences found.

    Reuses ``2`` from "found a non-conformant item": to CI, "behaviour changed"
    and "violates the spec" are the same event -- this commit needs a human.
    Same trade-off as ``check``, documented in ``exits.py``.
    """
    try:
        before_contract = build_contract(iter_frames(before))
        after_contract = build_contract(iter_frames(after))
    except RecordError as exc:
        err_console.print(styled_lines([(str(exc), "mcpdump.err")]))
        return EXIT_USAGE

    result = diff_contracts(before_contract, after_contract)

    if as_json:
        payload: dict[str, Any] = {"before": before, "after": after, **result.to_dict()}
        # --json is machine-read, so it must use plain print, not Rich.
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_NOT_CONFORMANT if result.changes else EXIT_OK

    render_diff(
        console, result, before_label=before, after_label=after, width=console.width
    )

    if result.changes:
        render_next_steps(
            console,
            hint_lines([
                f"mcpdump replay {after}",
                f"mcpdump diff {before} {after} --json",
            ]),
            title=t("diff.next_steps"),
        )
    return EXIT_NOT_CONFORMANT if result.changes else EXIT_OK

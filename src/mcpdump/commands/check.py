"""mcpdump check -- a protocol health check for any MCP server.

This command's stance is **evidence**, not scoring: every failure carries the
spec clause and the raw frames, so the user can file an issue with the report
without going back to the spec. Hence no "score", only pass / fail / skipped,
plus the source of each failure.

Exit codes follow the global contract: 0 all passed; 2 non-conformant items
(a failed connection or handshake also counts, since to CI both mean "this
server is unusable right now"); 1 usage error; 3 timeout; 4 environment error.

Three **external-consumer** outputs, mutually exclusive: ``--json`` (machine),
``--badge`` (README), ``--markdown`` (PR comment or CI summary). All three
**bypass Rich and use plain print** -- Rich wraps, colours, and indents, which
turns pasted output into broken Markdown or unparseable JSON.
"""

from __future__ import annotations

import json

from ..core.jsonrpc import JsonRpcError
from ..core.transport import ServerGoneError
from ..exits import (
    EXIT_ENVIRONMENT,
    EXIT_NOT_CONFORMANT,
    EXIT_OK,
    EXIT_TIMEOUT,
    EXIT_USAGE,
)
from ..i18n import t
from ..runtime import SessionOptions, open_session
from ..services.badge import badge_markdown
from ..services.markdown import report_markdown
from ..services.report import render_check_report
from ..services.runner import run_checks
from ..ui import console, err_console, render_error, styled_lines


def run(
    opts: SessionOptions,
    *,
    as_json: bool = False,
    badge: bool = False,
    markdown: bool = False,
) -> int:
    # Only one output may be chosen. Hardcoding pairwise comparisons would need
    # edits in three places when a fourth output appears, and one would be
    # missed -- so count how many were selected instead.
    modes = [
        name
        for name, enabled in (
            ("--json", as_json),
            ("--badge", badge),
            ("--markdown", markdown),
        )
        if enabled
    ]
    if len(modes) > 1:
        # Name the flags the user actually passed: any pair or triple is
        # possible, so a hardcoded pair would point at the wrong one.
        flags = ", ".join(f"`{name}`" for name in modes)
        err_console.print(
            styled_lines([(t("check.mutually_exclusive", flags=flags), "mcpdump.err")])
        )
        return EXIT_USAGE

    try:
        with open_session(opts) as session:
            report = run_checks(opts, session)

            if badge:
                # The badge goes into a README, so plain print is required.
                # Rich would wrap, colour, and indent it into broken Markdown.
                print(badge_markdown(report))
            elif markdown:
                # Same. ``report_markdown`` already ends with a newline; adding
                # another would leave a blank line at the end of the comment.
                print(report_markdown(report), end="")
            elif as_json:
                # Likewise machine-read, so it cannot go through Rich.
                print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
            else:
                render_check_report(console, report, transport=opts.describe_transport())

            return EXIT_OK if report.conformant else EXIT_NOT_CONFORMANT

    except JsonRpcError as exc:
        render_error(err_console, exc)
        return EXIT_NOT_CONFORMANT
    except ServerGoneError as exc:
        # The server died before finishing the handshake, which is the same
        # situation as "cannot connect": it is unusable right now.
        render_error(err_console, exc)
        return EXIT_NOT_CONFORMANT
    except TimeoutError as exc:
        render_error(err_console, exc)
        return EXIT_TIMEOUT
    except RuntimeError as exc:
        render_error(err_console, exc)
        return EXIT_ENVIRONMENT

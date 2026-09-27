"""mcpdump discover -- find the MCP servers already configured on this machine and
connect to one directly.

**What it solves**: the biggest obstacle to trying mcpdump is not installing it,
it is having to type the launch command again. That command is already written in
Claude Desktop's or Cursor's config -- so just find it.

**This module contains no discovery logic.** Scanning, path resolution, and
diagnosis all live in ``services/discovery.py``. Three things happen here: lay the
results out for a human, let the user choose, and hand the choice to ``ls``.

**On "unavailable" servers: ``--use`` connects anyway.** Static checks can only
advise, never replace a real connection -- the command may live on another PATH,
or an environment variable may be set only in an interactive shell. Pre-emptively
overruling the user is worse than letting them see the real error, so these
verdicts only affect **ordering and hints**.

**Why ``ls`` is called directly**: ``discover``'s output is ``ls``'s input and
both sit in the command layer (same-layer references do not violate the dependency
direction). Passing the selection back to ``cli`` for dispatch would only scatter
"what happens after choosing" across two files.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..exits import EXIT_OK, EXIT_USAGE
from ..i18n import t
from ..runtime import SessionOptions, open_session
from ..services import discovery as disc
from ..ui import (
    SYMBOLS,
    console,
    err_console,
    hint_lines,
    justify_ends,
    ljust,
    render_next_steps,
    styled_lines,
    truncate,
)
from . import ls as ls_cmd

#: Cap on probing one server. Better to report a timeout than let discover hang
#: for minutes.
PROBE_TIMEOUT = 10.0

#: Minimum / maximum width of the name column in the list.
_NAME_MIN = 12
_NAME_MAX = 32

_STATUS_SYMBOL = {
    disc.Availability.READY: SYMBOLS.pending,
    disc.Availability.VERIFIED: SYMBOLS.success,
    disc.Availability.BLOCKED: SYMBOLS.failure,
}

_STATUS_STYLE = {
    disc.Availability.READY: "mcpdump.meta",
    disc.Availability.VERIFIED: "mcpdump.ok",
    disc.Availability.BLOCKED: "mcpdump.warn",
}

#: Unverified servers sort after verified ones: the list order is itself advice.
_STATUS_RANK = {
    disc.Availability.VERIFIED: 0,
    disc.Availability.READY: 1,
    disc.Availability.BLOCKED: 2,
}


def problem_text(issue: disc.Issue) -> str:
    """Render one problem in plain language. ``detail`` is the part that makes it
    actionable."""
    return t(f"discover.problem.{issue.problem.value}", detail=issue.detail or "")


def order(diags: Sequence[disc.Diagnosis]) -> list[disc.Diagnosis]:
    """Usable ones first, then by name within each tier.

    Ordering is not decoration: a glance should answer "which do I try first".
    """
    return sorted(
        diags,
        key=lambda d: (_STATUS_RANK[d.availability], d.entry.name.lower()),
    )


# ---------------------------------------------------------------- probing


def _probe_stdio(entry: disc.ServerEntry) -> list[disc.Issue]:
    """Run a real handshake. Any exception becomes an issue; a probe must never
    take the scan down with it."""
    opts = SessionOptions(
        server=entry.launch_command(),
        argv=list(entry.command),
        timeout=PROBE_TIMEOUT,
        show_server_stderr=False,
        env=dict(entry.env),
        cwd=entry.cwd,
    )
    try:
        with open_session(opts) as session:
            session.list_tools()
    except Exception as exc:  # noqa: BLE001 - a probe must never abort the scan, see module docstring
        return [disc.Issue(disc.Problem.PROBE_FAILED, f"{type(exc).__name__}: {exc}")]
    return []


def make_prober(*, announce: bool = False) -> disc.Prober:
    """Build the probe callback: a real handshake for stdio, port-only for URLs."""

    def _probe(entry: disc.ServerEntry) -> list[disc.Issue]:
        if announce:
            err_console.print(styled_lines([
                (t("discover.probing", name=entry.name), "mcpdump.dim"),
            ]))
        if entry.kind is not disc.TransportKind.STDIO:
            return list(disc.tcp_probe(entry))
        return _probe_stdio(entry)

    return _probe


# ---------------------------------------------------------------- rendering


def _name_width(diags: Sequence[disc.Diagnosis]) -> int:
    widest = max((len(d.entry.name) for d in diags), default=_NAME_MIN)
    return max(_NAME_MIN, min(_NAME_MAX, widest))


def _server_lines(diags: Sequence[disc.Diagnosis], *, width: int) -> list[tuple[str, str]]:
    """One line per server, plus a reason line for any that has problems."""
    name_width = _name_width(diags)
    out: list[tuple[str, str]] = []
    for index, diag in enumerate(diags, start=1):
        entry = diag.entry
        left = f"{index:>2}  {ljust(entry.name, name_width)}  {entry.origin}"
        status = t(f"discover.status.{diag.availability.value}")
        right = f"{_STATUS_SYMBOL[diag.availability]} {status}"
        out.append((justify_ends(left, right, width=width), _STATUS_STYLE[diag.availability]))
        for issue in diag.issues:
            detail = f"     {SYMBOLS.warning} {problem_text(issue)}"
            out.append((truncate(detail, width), "mcpdump.dim"))
    return out


def _footer_lines(
    reports: Sequence[disc.ClientReport],
    *,
    probed: bool,
) -> list[tuple[str, str]]:
    """Scan scope: which clients are absent, which paths are unreliable, which
    entries could not be read.

    Entry-level problems **must be listed one by one**, not summarized as a count.
    "1 config file could not be fully read" without saying which one tells the user
    nothing -- and "configured but written wrong" is exactly what this feature
    should surface.
    """
    out: list[tuple[str, str]] = []
    missing = [r.spec.name for r in reports if not r.found]
    if missing:
        out.append((t("discover.not_found", clients=" · ".join(missing)), "mcpdump.dim"))

    uncertain = sorted({r.spec.name for r in reports if r.spec.uncertain})
    if uncertain:
        out.append((t("discover.uncertain_note", clients=" · ".join(uncertain)), "mcpdump.warn"))

    broken = [(report, issue) for report in reports for issue in report.issues]
    if broken:
        out.append((t("discover.problem_note", count=len(broken)), "mcpdump.warn"))
        for report, issue in broken:
            where = report.spec.name
            if report.path is not None:
                where = f"{where} · {report.path.name}"
            out.append((f"  {where}", "mcpdump.label"))
            out.append((f"    {SYMBOLS.warning} {problem_text(issue)}", "mcpdump.dim"))

    if not probed:
        out.append((t("discover.static_note"), "mcpdump.dim"))
    return out


def _hint_lines(diags: Sequence[disc.Diagnosis]) -> list[str]:
    """Next steps that can be copied verbatim, preferring one already ready.

    **The full launch command is deliberately not echoed**: its arguments may
    already contain quotes, and wrapping them again for the user to copy is wrong
    more often than not (Windows paths with nested quotes especially so).
    ``--use <name>`` is equivalent and cannot go wrong -- the teaching value of
    echoing the command is not worth that hazard.
    """
    ready = next((d for d in diags if d.usable), None)
    if ready is None:
        return hint_lines(["mcpdump discover --probe"])
    return hint_lines([
        f"mcpdump discover --use {ready.entry.name}",
        "mcpdump discover --probe",
    ])


def render(
    diags: Sequence[disc.Diagnosis],
    reports: Sequence[disc.ClientReport],
    *,
    probed: bool,
) -> None:
    found = sum(1 for r in reports if r.found)
    body: list[tuple[str, str]] = [
        (t("discover.scanned", clients=found, servers=len(diags)), "mcpdump.dim"),
        ("", ""),
        (t("discover.title"), "mcpdump.label"),
    ]
    body.extend(_server_lines(diags, width=console.width))
    footer = _footer_lines(reports, probed=probed)
    if footer:
        body.append(("", ""))
        body.extend(footer)

    console.print(styled_lines(body))
    if diags:
        console.print()
        render_next_steps(console, _hint_lines(diags))


# ---------------------------------------------------------------- selection


def pick(diags: Sequence[disc.Diagnosis]) -> disc.Diagnosis | None:
    """Let the user choose by number. Returns ``None`` when non-interactive
    (a pipe, or CI).

    The ``sys.stdin.isatty()`` check cannot be dropped: without it, running
    ``mcpdump discover`` in CI blocks waiting for input, and the error message
    never mentions that it is waiting.
    """
    if not sys.stdin.isatty() or not diags:
        return None
    while True:
        try:
            raw = input(t("discover.pick", count=len(diags)))
        except (EOFError, KeyboardInterrupt):
            console.print()
            return None
        choice = raw.strip().lower()
        if choice in {"", "q"}:
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(diags):
            return diags[int(choice) - 1]
        console.print(styled_lines([(t("discover.pick_invalid"), "mcpdump.warn")]))


# ---------------------------------------------------------------- connecting


def connect(entry: disc.ServerEntry) -> int:
    """Hand the discovered server to ``ls`` -- the entire reason ``discover`` exists."""
    if entry.kind is not disc.TransportKind.STDIO:
        problem = disc.Issue(disc.Problem.TRANSPORT_UNSUPPORTED, entry.kind.value)
        err_console.print(styled_lines([(problem_text(problem), "mcpdump.err")]))
        return EXIT_USAGE
    return ls_cmd.run(
        SessionOptions(
            server=entry.launch_command(),
            # The config's argv is already split, so ls uses it as-is: joining it
            # into a string and splitting again could break a path with a space or
            # a backslash.
            argv=list(entry.command),
            env=dict(entry.env),
            cwd=entry.cwd,
        )
    )


# ---------------------------------------------------------------- entry points


def _select_specs(clients: Sequence[str] | None) -> tuple[disc.ClientSpec, ...] | None:
    """``--client`` filtering. Name or id both work, case-insensitively."""
    if not clients:
        return None
    wanted = {name.strip().lower() for name in clients}
    known = {
        key: spec
        for spec in disc.CLIENTS
        for key in (spec.id.lower(), spec.name.lower())
    }
    unknown = sorted(wanted - known.keys())
    if unknown:
        err_console.print(styled_lines([
            (
                t(
                    "discover.unknown_client",
                    name=unknown[0],
                    names=" · ".join(sorted({s.id for s in disc.CLIENTS})),
                ),
                "mcpdump.err",
            ),
        ]))
        return ()
    return tuple({spec.id: spec for spec in (known[key] for key in wanted)}.values())


def _prepare(
    project: str | None,
    clients: Sequence[str] | None,
    probe: bool,
) -> tuple[list[disc.Diagnosis], tuple[disc.ClientReport, ...]] | None:
    """Scan and diagnose. Returns ``None`` on a bad ``--client`` (already reported)."""
    specs = _select_specs(clients)
    if specs is not None and not specs:
        return None
    reports = disc.scan(
        project_dir=Path(project) if project else None,
        clients=specs,
    )
    diags = order(
        disc.diagnose(reports, probe=make_prober(announce=probe) if probe else None)
    )
    return diags, reports


def run(
    *,
    as_json: bool = False,
    use: str | None = None,
    clients: Sequence[str] | None = None,
    project: str | None = None,
    probe: bool = False,
) -> int:
    prepared = _prepare(project, clients, probe)
    if prepared is None:
        return EXIT_USAGE
    diags, reports = prepared

    if as_json:
        # --json is machine-read: plain print, never Rich.
        payload: dict[str, Any] = {
            "clients": [report.to_dict() for report in reports],
            "servers": [diag.to_dict() for diag in diags],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK

    if not diags:
        _render_empty(reports)
        return EXIT_OK

    render(diags, reports, probed=probe)

    if use is None:
        target = pick(diags)
    else:
        target = _by_name(diags, use)
        if target is None:
            return EXIT_USAGE

    if target is None:
        return EXIT_OK
    console.print()
    return connect(target.entry)


def run_bare() -> bool:
    """Default behaviour when ``mcpdump`` runs bare, with no subcommand.

    ``True`` means "already handled"; ``False`` means no server was found on this
    machine and the caller should fall back to showing help -- which is genuinely
    what the user needs in that case.
    """
    prepared = _prepare(None, None, False)
    if prepared is None:  # pragma: no cover - cannot happen without clients
        return False
    diags, reports = prepared
    if not diags:
        return False
    render(diags, reports, probed=False)
    target = pick(diags)
    if target is not None:
        console.print()
        connect(target.entry)
    return True


def _by_name(diags: Sequence[disc.Diagnosis], name: str) -> disc.Diagnosis | None:
    """Find exactly one by name. Reports and returns ``None`` if missing or ambiguous.

    Same-named servers across clients are the norm (one filesystem server
    configured in both Claude Desktop and Cursor). In that case **do not guess** --
    a wrong guess connects to a different server than expected, whereas an error
    only costs one extra ``--client``.
    """
    matches = [d for d in diags if d.entry.name == name]
    if not matches:
        err_console.print(styled_lines([
            (t("discover.unknown_name", name=name), "mcpdump.err"),
            (
                t("discover.candidates", names=" · ".join(d.entry.name for d in diags)),
                "mcpdump.dim",
            ),
        ]))
        return None
    if len(matches) > 1:
        err_console.print(styled_lines([
            (t("discover.ambiguous", name=name, count=len(matches)), "mcpdump.err"),
            (
                t(
                    "discover.candidates",
                    names=" · ".join(f"{d.entry.client_id}:{d.entry.name}" for d in matches),
                ),
                "mcpdump.dim",
            ),
        ]))
        return None
    return matches[0]


def _render_empty(reports: Sequence[disc.ClientReport]) -> None:
    """No server found. Not an error -- state plainly what to type next."""
    body: list[tuple[str, str]] = [
        (
            t("discover.scanned", clients=sum(1 for r in reports if r.found), servers=0),
            "mcpdump.dim",
        ),
        ("", ""),
        (t("discover.none_found"), "mcpdump.warn"),
        (t("discover.none_found_hint"), "mcpdump.dim"),
    ]
    footer = _footer_lines(reports, probed=False)
    if footer:
        body.append(("", ""))
        body.extend(footer)
    console.print(styled_lines(body))
    console.print()
    render_next_steps(console, hint_lines([
        'mcpdump ls "npx -y @modelcontextprotocol/server-filesystem ."',
        "mcpdump discover --json",
    ]))


__all__ = [
    "PROBE_TIMEOUT",
    "connect",
    "make_prober",
    "order",
    "pick",
    "problem_text",
    "render",
    "run",
    "run_bare",
]

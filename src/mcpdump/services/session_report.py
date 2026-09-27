"""Human-readable forms of ``replay`` and ``diff``.

In ``services`` rather than ``ui`` because these renderers need to know about
``ReplayResult`` and ``ContractDiff``, while ``ui`` holds bottom-level
primitives. As in ``ui/wire.py``, ``build_*`` returns ``Text`` lines instead of
printing, so tests can assert on content without a Console.

Differences are ordered by severity, and latency regressions get their own
section because they carry units that would otherwise break the alignment.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from ..i18n import t
from ..ui import (
    SYMBOLS,
    display_width,
    format_duration,
    key_value_lines,
    truncate,
)
from .diff import (
    CHANGE_CAPABILITY,
    CHANGE_LATENCY,
    CHANGE_TOOL_ADDED,
    CHANGE_TOOL_REMOVED,
    CHANGE_TOOL_SCHEMA,
    Change,
    Contract,
    ContractDiff,
)
from .replayer import ReplayResult, Step

__all__ = ["build_diff", "build_replay", "build_replay_summary", "render_diff", "render_replay"]

_LEVEL1 = "  "
_LEVEL2 = "    "

#: Symbol and colour per change kind. Added tools use ``+`` and removed ones ``-``,
#: matching git diff so there is nothing new to learn.
_CHANGE_STYLE: dict[str, tuple[str, str]] = {
    CHANGE_TOOL_ADDED: ("+", "mcpdump.ok"),
    CHANGE_TOOL_REMOVED: ("-", "mcpdump.err"),
    CHANGE_TOOL_SCHEMA: ("~", "mcpdump.warn"),
    CHANGE_CAPABILITY: ("~", "mcpdump.warn"),
    CHANGE_LATENCY: ("!", "mcpdump.warn"),
}

#: Sort weight, lower first: removals matter most, then contract changes, then latency.
_CHANGE_ORDER: dict[str, int] = {
    CHANGE_TOOL_REMOVED: 0,
    CHANGE_TOOL_SCHEMA: 1,
    CHANGE_CAPABILITY: 2,
    CHANGE_TOOL_ADDED: 3,
    CHANGE_LATENCY: 4,
}


# ---------------------------------------------------------------- replay


def build_replay_summary(result: ReplayResult, *, source: str) -> list[Text]:
    """The replay header: what this recording is and how much it holds."""
    stats = result.stats
    rows: list[tuple[str, str | None]] = [
        (t("replay.label.source"), source),
        (t("replay.label.steps"), str(len(result.steps))),
        (t("replay.label.exchanges"), str(stats.exchanges)),
        (t("replay.label.bytes"), _bytes_text(stats.bytes)),
    ]
    if stats.notifications:
        rows.append((t("replay.label.notifications"), str(stats.notifications)))
    if stats.orphan:
        rows.append((t("replay.label.orphan"), str(stats.orphan)))
    if stats.truncated:
        rows.append((t("replay.label.truncated"), str(stats.truncated)))
    if stats.dangling:
        rows.append((t("replay.label.dangling"), str(stats.dangling)))
    return [Text(line) for line in key_value_lines(rows, key_width=0)]


def _bytes_text(count: int) -> str:
    """Convert a byte count to a readable unit; the conversion belongs here, not
    in the text table.
    """
    if count < 1024:
        return t("replay.bytes_b", count=count)
    if count < 1024 * 1024:
        return t("replay.bytes_kb", count=count / 1024)
    return t("replay.bytes_mb", count=count / (1024 * 1024))


def _symbol_slot() -> int:
    """Fixed width for the symbol column.

    ``⇢`` (a notification) and ``→ ←`` (a round trip) differ in width, and
    without a fixed column the labels drift. Measured against the active symbol
    table, so ``MCPDUMP_ASCII`` stays aligned too.
    """
    return max(
        display_width(f"{SYMBOLS.request} {SYMBOLS.response}"),
        display_width(f"{SYMBOLS.request} {SYMBOLS.failure}"),
        display_width(SYMBOLS.notify),
    )


def _step_line(step: Step, *, width: int) -> Text:
    """One step per line, shared by ``--step`` and normal replay so the visual
    language stays the same.

    Symbols come from ``ui.symbols`` and are chosen by direction: a round trip is
    ``→ ←``, a notification one ``→``, an orphan response a single ``⇢``, and a
    dangling request (no reply) ``→ ✗``.
    """
    if step.orphan:
        symbol, style = SYMBOLS.notify, "mcpdump.warn"
        note = t("replay.orphan_mark")
    elif step.notification:
        # A notification is one-way by design, so the notify symbol keeps it
        # apart from a request that got no reply.
        symbol, style = SYMBOLS.notify, "mcpdump.dim"
        note = ""
    elif step.response_line is None:
        symbol, style = f"{SYMBOLS.request} {SYMBOLS.failure}", "mcpdump.err"
        note = t("replay.dangling_mark")
    elif step.request_line is None:
        symbol, style = SYMBOLS.response, "mcpdump.res"
        note = ""
    else:
        symbol, style = f"{SYMBOLS.request} {SYMBOLS.response}", "mcpdump.res"
        note = t("replay.truncated_mark") if step.truncated else ""

    pad = " " * (_symbol_slot() - display_width(symbol))
    head = f"{_LEVEL1}{step.index:>4}  {symbol}{pad}  {step.label}"
    text = Text(head, style=style)
    if step.elapsed_ms is not None:
        right = format_duration(step.elapsed_ms)
        gap = max(2, width - display_width(head) - display_width(right))
        text.append(" " * gap)
        text.append(right, style="mcpdump.meta")
    if note:
        text.append("  " + note, style="mcpdump.warn")
    return text


def build_replay(
    result: ReplayResult,
    *,
    source: str,
    width: int,
    limit: int | None = None,
    step_mode: bool = False,
) -> list[Text]:
    """The replay view. With ``limit``, only the first N steps are shown and the
    number omitted is stated.

    Never truncate silently: a user treating the replay as evidence is worse off
    being shown 9000 fewer steps without being told.
    """
    out: list[Text] = build_replay_summary(result, source=source)
    if not result.steps:
        out.append(Text())
        out.append(Text(t("replay.empty"), style="mcpdump.dim"))
        return out

    out.append(Text())
    shown = result.steps if limit is None else result.steps[:limit]
    body = 4
    for step in shown:
        out.append(_step_line(step, width=width))
        if step_mode:
            # In step mode include the raw message, or stepping shows no more
            # than a normal replay.
            raw = step.request_line or step.response_line or ""
            out.append(Text(f"{_LEVEL2}{truncate(raw, max(1, width - body))}", style="mcpdump.dim"))

    hidden = len(result.steps) - len(shown)
    if hidden:
        out.append(Text())
        out.append(Text(t("replay.omitted", count=hidden), style="mcpdump.dim"))
    return out


def render_replay(
    console: Console,
    result: ReplayResult,
    *,
    source: str,
    width: int | None = None,
    limit: int | None = None,
    step_mode: bool = False,
) -> None:
    columns = width or console.width
    lines = build_replay(
        result, source=source, width=columns, limit=limit, step_mode=step_mode
    )
    for line in lines:
        console.print(line, soft_wrap=True)


# ---------------------------------------------------------------- diff


def _change_line(change: Change) -> Text:
    """One change, with ``detail`` on its own indented line rather than inline.

    Inline detail would swallow the most important part whenever a long path met
    a long subject; wrapping means detail is never lost.
    """
    symbol, style = _CHANGE_STYLE.get(change.kind, ("~", "mcpdump.meta"))
    out = Text(f"{_LEVEL1}{symbol} {change.subject}", style=style)
    if change.detail:
        out.append("\n")
        out.append(f"{_LEVEL2}{change.detail}", style="mcpdump.dim")
    return out


def build_diff(
    diff: ContractDiff, *, before_label: str, after_label: str, width: int
) -> list[Text]:
    """The diff view. With no differences it says so rather than printing nothing."""
    out: list[Text] = [
        Text(t("diff.headers", before=before_label, after=after_label), style="mcpdump.label")
    ]

    for label, contract in ((before_label, diff.before), (after_label, diff.after)):
        out.append(Text(f"{_LEVEL1}{label}: {_one_line_contract(contract)}", style="mcpdump.dim"))

    if diff.identical:
        out.append(Text())
        out.append(Text(t("diff.identical"), style="mcpdump.ok"))
        return out

    ordered = sorted(
        diff.changes,
        key=lambda change: (_CHANGE_ORDER.get(change.kind, 99), change.subject),
    )

    # Latency regressions get their own section: they carry units and would break
    # the alignment.
    structural = [c for c in ordered if c.kind != CHANGE_LATENCY]
    latency = [c for c in ordered if c.kind == CHANGE_LATENCY]

    if structural:
        out.append(Text())
        out.append(Text(t("diff.section_structural"), style="mcpdump.label"))
        for change in structural:
            out.append(_change_line(change))

    if latency:
        out.append(Text())
        out.append(Text(t("diff.section_latency"), style="mcpdump.label"))
        for change in latency:
            out.append(_change_line(change))

    out.append(Text())
    out.append(Text(t("diff.total", count=len(diff.changes)), style="mcpdump.meta"))
    return out


def _one_line_contract(contract: Contract) -> str:
    """A single line such as ``echo-server v1.2 · 3 tools · tools,resources``."""
    name = contract.server_name or t("diff.unnamed")
    version = f" v{contract.server_version}" if contract.server_version else ""
    tools = t("diff.tool_count", count=len(contract.tools))
    caps = ", ".join(sorted(contract.capabilities)) or t("diff.no_capabilities")
    return f"{name}{version} · {tools} · {caps}"


def render_diff(
    console: Console,
    diff: ContractDiff,
    *,
    before_label: str,
    after_label: str,
    width: int | None = None,
) -> None:
    columns = width or console.width
    for line in build_diff(
        diff, before_label=before_label, after_label=after_label, width=columns
    ):
        console.print(line, soft_wrap=True)

"""The human-readable form of a conformance report.

Lives in ``services`` rather than ``ui`` because it needs to know about
``CheckReport``, and ``ui`` importing ``services`` would make
``runtime → ui → services → runtime`` a cycle.

As in ``ui/wire.py``, ``build_report()`` returns ``Text`` lines instead of
printing, so tests can assert on content and column widths without a Console.

A passing check takes one line; ``spec`` is printed only on failure.
"""

from __future__ import annotations

from rich.console import Console
from rich.text import Text

from ..i18n import t
from ..ui import SYMBOLS, display_width, render_server_header, truncate
from .checks import CheckResult, Finding, Severity, Status
from .runner import CheckReport

__all__ = ["build_report", "render_check_report"]

#: Symbol and colour per status. ``pending`` marks a skip, which is neither a
#: pass nor a failure.
_STATUS_STYLE: dict[Status, tuple[str, str]] = {
    Status.PASSED: (SYMBOLS.success, "mcpdump.ok"),
    Status.FAILED: (SYMBOLS.failure, "mcpdump.err"),
    Status.SKIPPED: (SYMBOLS.pending, "mcpdump.dim"),
}

#: Severity drives the colour: red for errors, yellow for warnings, grey for notes.
_SEVERITY_STYLE: dict[Severity, str] = {
    Severity.ERROR: "mcpdump.err",
    Severity.WARNING: "mcpdump.warn",
    Severity.INFO: "mcpdump.meta",
}

_LEVEL1 = "  "
_LEVEL2 = "    "
_LEVEL3 = "      "


def _one_line(text: str) -> str:
    """Flatten evidence onto one line; anything longer pushes other checks off screen."""
    return " ".join(text.split())


def _finding_lines(finding: Finding, *, width: int) -> list[Text]:
    out = [Text(_LEVEL2 + finding.message, style=_SEVERITY_STYLE[finding.severity])]
    if finding.evidence:
        prefix = t("check.evidence", evidence="")
        budget = max(1, width - display_width(_LEVEL3) - display_width(prefix))
        body = truncate(_one_line(finding.evidence), budget)
        out.append(Text(_LEVEL3 + t("check.evidence", evidence=body), style="mcpdump.dim"))
    return out


def _check_lines(result: CheckResult, *, width: int) -> list[Text]:
    symbol, style = _STATUS_STYLE[result.status]
    head = Text()
    head.append(f"{symbol} ", style=style)
    head.append(result.id, style="mcpdump.brand")
    head.append(f"  {result.title}", style="mcpdump.dim")
    lines = [head]

    if result.status is Status.SKIPPED:
        reason = result.skipped or ""
        lines.append(Text(_LEVEL1 + t("check.skipped_reason", reason=reason), style="mcpdump.dim"))
        return lines

    if result.status is Status.FAILED:
        lines.append(Text(_LEVEL1 + t("check.spec", spec=result.spec), style="mcpdump.dim"))
    for finding in result.findings:
        lines.extend(_finding_lines(finding, width=width))
    return lines


def build_report(report: CheckReport, *, width: int) -> list[Text]:
    """Build every line of the report. Nothing is printed; ``render_check_report`` does that."""
    lines = [
        Text(t("check.title"), style="mcpdump.label"),
        Text(
            t(
                "check.summary",
                total=report.total,
                passed=report.passed,
                failed=report.failed,
                skipped=report.skipped,
            ),
            style="mcpdump.dim",
        ),
    ]
    if report.warnings:
        lines.append(Text(t("check.warnings", count=report.warnings), style="mcpdump.warn"))

    for result in report.results:
        lines.append(Text())
        lines.extend(_check_lines(result, width=width))

    verdict = (
        t("check.verdict.passed", total=report.total)
        if report.conformant
        else t("check.verdict.failed", failed=report.failed, total=report.total)
    )
    verdict_line = Text()
    if report.conformant:
        verdict_line.append(f"{SYMBOLS.success} ", style="mcpdump.ok")
    else:
        verdict_line.append(f"{SYMBOLS.failure} ", style="mcpdump.err")
    verdict_line.append(verdict, style="mcpdump.label")
    lines.append(Text())
    lines.append(verdict_line)
    return lines


def render_check_report(console: Console, report: CheckReport, *, transport: str) -> None:
    """Print the report: the identity panel shared with ``ls``, then the verdict."""
    render_server_header(console, report.server, transport)
    console.print()
    for line in build_report(report, width=console.width):
        console.print(line, soft_wrap=True)

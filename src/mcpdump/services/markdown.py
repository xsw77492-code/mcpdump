"""Markdown form of the conformance report, for PR comments and CI summaries.

Separate from ``report.py`` because that produces width-wrapped ``Text`` rendered
by Rich; Markdown must not depend on terminal width, carry ANSI, or import ``ui``.

Failures are expanded and passes collapsed: a PR comment is worth reading for
which checks failed and why. Evidence sits inside a collapsed block, since a
``tools/list`` response can run to hundreds of KB. The fence length is computed
rather than fixed, because raw evidence may contain runs of backticks.
"""

from __future__ import annotations

from ..i18n import t
from .badge import HOMEPAGE, badge_markdown
from .checks import CheckResult, Finding, Status
from .runner import CheckReport

__all__ = ["report_markdown"]

#: Prefix per status. Emoji rather than ``SYMBOLS``: those are terminal glyphs
#: (``✓`` / ``✗``) that render small and inconsistently on GitHub.
_MARK: dict[Status, str] = {
    Status.PASSED: "✅",
    Status.FAILED: "❌",
    Status.SKIPPED: "⏭️",
}

#: Markdown metacharacters that need escaping. Only the ones that change inline
#: rendering: ``\`` `` ` `` ``*`` ``_`` ``[`` ``]`` ``<`` ``>`` ``|``.
#: ``.``, ``-``, ``+`` and ``!`` only matter at the start of a line or in link
#: syntax; escaping them makes the text harder to read and renders the same.
_MD_SPECIAL = "\\`*_[]<>|"


def _escape(text: str) -> str:
    """Escape Markdown metacharacters in the server's own description.

    ``name``, ``version`` and ``protocolVersion`` come from the server, so a
    server called ``a*b`` would italicise the rest of the line.
    """
    return "".join(f"\\{char}" if char in _MD_SPECIAL else char for char in text)


def _fence(content: str) -> str:
    """Pick a code fence long enough for ``content``: strictly longer than the
    longest run of backticks in the content, or that run ends the block early.
    """
    longest = run = 0
    for char in content:
        run = run + 1 if char == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _add(lines: list[str], block: list[str]) -> None:
    """Append a block, keeping exactly one blank line between blocks.

    Inconsistent blank lines are the usual bug in hand-assembled Markdown, and
    centralising it keeps the spacing independent of how many blocks there are.
    """
    if lines and lines[-1] != "":
        lines.append("")
    lines.extend(block)


def _evidence_block(finding: Finding) -> list[str]:
    if not finding.evidence:
        return []
    fence = _fence(finding.evidence)
    return [
        # The leading blank line matters: this block follows the finding's line
        # directly, and without it the text is treated as a continuation and the
        # collapsed block does not form.
        "",
        "<details><summary>evidence</summary>",
        "",
        fence,
        finding.evidence,
        fence,
        "",
        "</details>",
    ]


def _failed_block(result: CheckResult) -> list[str]:
    lines = [
        f"**`{result.id}`** — {result.title}",
        "",
        f"- {t('check.spec', spec=f'`{result.spec}`')}",
    ]
    for finding in result.findings:
        lines.append(f"- **{finding.severity.value.upper()}** {finding.message}")
        lines.extend(_evidence_block(finding))
    return lines


def _identity(report: CheckReport) -> str:
    """Identity line. The capability list may be empty, in which case no trailing
    separator is left.
    """
    base = t(
        "md.server",
        name=_escape(report.server.name),
        version=_escape(report.server.version),
        protocol=_escape(report.server.protocol_version),
    )
    if not report.server.capabilities:
        return base
    names = ", ".join(_escape(name) for name in sorted(report.server.capabilities))
    return f"{base} · {names}"


def report_markdown(report: CheckReport, *, link: str = HOMEPAGE) -> str:
    """Render the report as Markdown. Nothing is printed; the caller decides that."""
    lines: list[str] = [
        f"### {_MARK[Status.PASSED if report.conformant else Status.FAILED]} "
        f"{t('badge.label')} — {report.score}",
        "",
        badge_markdown(report, link=link),
        "",
        _identity(report),
        "",
        "`"
        + t(
            "check.summary",
            total=report.total,
            passed=report.passed,
            failed=report.failed,
            skipped=report.skipped,
        )
        + "`",
    ]

    failed = [item for item in report.results if item.status is Status.FAILED]
    if failed:
        _add(lines, [f"#### {t('md.failed', count=len(failed))}"])
        for result in failed:
            _add(lines, _failed_block(result))

    passed = [item for item in report.results if item.status is Status.PASSED]
    if passed:
        # ``<details>`` needs a blank line before it: flush against the previous
        # line it is absorbed into that paragraph and renders as plain text.
        # ``_add`` supplies the blank line.
        _add(lines, [
            f"<details><summary>{t('md.passing_details', count=len(passed))}</summary>",
            "",
            *[f"- {_MARK[Status.PASSED]} `{item.id}` — {item.title}" for item in passed],
            "",
            "</details>",
        ])

    skipped = [item for item in report.results if item.status is Status.SKIPPED]
    if skipped:
        _add(lines, [f"#### {t('md.skipped', count=len(skipped))}"])
        _add(lines, [
            f"- {_MARK[Status.SKIPPED]} `{item.id}` — "
            + t("check.skipped_reason", reason=item.skipped or "")
            for item in skipped
        ])

    _add(lines, ["---", t("md.footer", link=link)])
    return "\n".join(lines) + "\n"

"""Tests for the Markdown report.

``report_markdown()`` returns a string, so the byte shape can be asserted directly: no ANSI
or wrapping, a fence that outgrows backticks in evidence, one trailing newline.
"""

from __future__ import annotations

from mcpdump import i18n
from mcpdump.core.session import ServerInfo
from mcpdump.services.checks import CheckResult, Finding, Severity
from mcpdump.services.markdown import report_markdown
from mcpdump.services.runner import CheckReport


def _report(
    *results: CheckResult,
    capabilities: dict[str, object] | None = None,
    name: str = "demo",
    version: str = "1.0",
) -> CheckReport:
    server = ServerInfo(
        name=name,
        version=version,
        protocol_version="2025-06-18",
        capabilities=capabilities if capabilities is not None else {"tools": {}},
    )
    return CheckReport(server, list(results))


def _passed(check_id: str = "a-check") -> CheckResult:
    return CheckResult(check_id, f"{check_id} title", "spec clause")


def _failed(
    check_id: str = "a-check",
    *,
    message: str = "something is wrong",
    evidence: str | None = '{"jsonrpc":"2.0"}',
) -> CheckResult:
    return CheckResult(
        check_id,
        f"{check_id} title",
        "spec clause",
        [Finding(Severity.ERROR, message, evidence)],
    )


def _skipped(check_id: str = "a-check", reason: str = "no tools capability") -> CheckResult:
    return CheckResult(check_id, f"{check_id} title", "spec clause", skipped=reason)


def _evidence_fences(text: str, index: int = 0) -> list[str]:
    """The opening and closing fence lines of the ``index``-th evidence block.

    Fences are taken by position, not by "the whole line is backticks": the evidence may
    itself be a line of backticks, which would hide the real fences.
    """
    lines = text.splitlines()
    marker = "<details><summary>evidence</summary>"
    start = [i for i, line in enumerate(lines) if line == marker][index]
    end = lines.index("</details>", start)
    fences = [line for line in lines[start:end] if line and set(line) == {"`"}]
    return [fences[0], fences[-1]]


class TestHeader:
    def test_starts_with_a_status_heading(self) -> None:
        assert report_markdown(_report(_passed())).startswith("### ✅ ")
        assert report_markdown(_report(_failed())).startswith("### ❌ ")

    def test_heading_carries_the_score(self) -> None:
        assert "1/1" in report_markdown(_report(_passed())).splitlines()[0]
        assert "0/1" in report_markdown(_report(_failed())).splitlines()[0]

    def test_carries_the_badge(self) -> None:
        text = report_markdown(_report(_passed()))
        assert "[![" in text
        assert "img.shields.io" in text

    def test_link_reaches_both_the_badge_and_the_footer(self) -> None:
        text = report_markdown(_report(_passed()), link="https://example.com/x")
        assert text.count("https://example.com/x") == 2

    def test_identity_names_the_server_and_the_protocol(self) -> None:
        line = report_markdown(_report(_passed())).splitlines()[4]
        assert "**demo**" in line
        assert "1.0" in line
        assert "`2025-06-18`" in line
        assert "tools" in line

    def test_identity_has_no_dangling_separator_without_capabilities(self) -> None:
        """An orphan ``·`` when the capability list is empty looks worse than nothing."""
        line = report_markdown(_report(_passed(), capabilities={})).splitlines()[4]
        assert line.endswith("`2025-06-18`")

    def test_identity_escapes_what_the_server_says(self) -> None:
        """A ``*`` or ``[`` in the server's self-description would swallow the whole line."""
        report = _report(
            _passed(),
            capabilities={"tools": {}, "a|b": {}},
            name="a*b",
            version="1.0 [beta]",
        )
        line = report_markdown(report).splitlines()[4]
        assert r"**a\*b**" in line
        assert r"1.0 \[beta\]" in line
        assert r"a\|b" in line

    def test_summary_is_quoted_so_it_reads_as_one_unit(self) -> None:
        assert "`1 checks · 1 passed · 0 failed · 0 skipped`" in report_markdown(_report(_passed()))


class TestLayout:
    def test_passing_checks_are_collapsed(self) -> None:
        text = report_markdown(_report(_passed("alpha"), _passed("beta")))
        assert "<details><summary>Show 2 passing check(s)</summary>" in text
        assert "</details>" in text
        assert "- ✅ `alpha` — alpha title" in text
        assert "- ✅ `beta` — beta title" in text

    def test_no_failed_section_when_everything_passes(self) -> None:
        assert "#### Failed" not in report_markdown(_report(_passed()))

    def test_no_details_block_when_nothing_passes(self) -> None:
        assert "<details><summary>Show" not in report_markdown(_report(_failed()))

    def test_details_block_starts_a_fresh_paragraph(self) -> None:
        """A ``<details>`` flush against the previous line merges into that paragraph,
        and the block stops collapsing.
        """
        lines = report_markdown(_report(_passed())).splitlines()
        index = lines.index("<details><summary>Show 1 passing check(s)</summary>")
        assert lines[index - 1] == ""
        assert lines[lines.index("</details>") - 1] == ""

    def test_blocks_are_separated_by_exactly_one_blank_line(self) -> None:
        """The failed block ends with a blank line and the passing block starts with one,
        so adjacent blocks leave two.
        """
        text = report_markdown(_report(_failed("bad"), _passed("good"), _skipped("nope")))
        assert "\n\n\n" not in text

    def test_evidence_block_starts_a_fresh_paragraph(self) -> None:
        """It is appended straight after the finding line; flush against it, it reads as
        a continuation and the block does not form.
        """
        lines = report_markdown(_report(_failed())).splitlines()
        index = lines.index("<details><summary>evidence</summary>")
        assert lines[index - 1] == ""

    def test_failed_section_expands_every_finding(self) -> None:
        text = report_markdown(_report(_failed(message="boom", evidence="raw bytes")))
        assert "#### Failed (1)" in text
        assert "**`a-check`** — a-check title" in text
        assert "spec: `spec clause`" in text
        assert "- **ERROR** boom" in text
        assert "raw bytes" in text

    def test_failures_come_before_the_collapsed_passes(self) -> None:
        """The first screen of the comment must be all problems, not taken up by passing checks."""
        text = report_markdown(_report(_failed("bad"), _passed("good")))
        assert text.index("#### Failed") < text.index("<details><summary>Show")

    def test_multiple_failures_keep_the_report_order(self) -> None:
        text = report_markdown(_report(_failed("first"), _passed("mid"), _failed("last")))
        assert text.index("`first`") < text.index("`last`")

    def test_skipped_checks_explain_why(self) -> None:
        text = report_markdown(_report(_skipped(reason="no tools capability")))
        assert "#### Skipped (1)" in text
        assert "no tools capability" in text

    def test_skipped_counts_as_not_passed(self) -> None:
        """Skipped is not failed: the report stays conformant, but the score must say so."""
        text = report_markdown(_report(_passed("good"), _skipped("nope")))
        assert text.startswith("### ✅ ")
        assert "2 checks · 1 passed · 0 failed · 1 skipped" in text


class TestSeverity:
    def test_warning_does_not_count_as_a_failure(self) -> None:
        """Only ERROR fails a check: that is ``Status``'s rule, and the Markdown view
        cannot invent its own.
        """
        result = CheckResult(
            "a-check", "title", "spec", [Finding(Severity.WARNING, "heads up", None)]
        )
        text = report_markdown(_report(result))
        assert "#### Failed" not in text
        assert "<details><summary>Show 1 passing check(s)</summary>" in text

    def test_warning_severity_is_labelled(self) -> None:
        result = CheckResult(
            "a-check",
            "title",
            "spec",
            [Finding(Severity.WARNING, "heads up", None), Finding(Severity.ERROR, "boom", None)],
        )
        assert "- **WARNING** heads up" in report_markdown(_report(result))


class TestFence:
    def test_plain_evidence_uses_three_backticks(self) -> None:
        text = report_markdown(_report(_failed(evidence="plain text")))
        assert _evidence_fences(text) == ["```", "```"]

    def test_fence_grows_past_backticks_inside_the_evidence(self) -> None:
        """When ``` appears in the evidence, a three-backtick fence ends the block right there."""
        text = report_markdown(_report(_failed(evidence="see ``` in the payload")))
        assert _evidence_fences(text) == ["````", "````"]

    def test_fence_grows_to_any_run_length(self) -> None:
        text = report_markdown(_report(_failed(evidence="`" * 8)))
        assert _evidence_fences(text) == ["`" * 9, "`" * 9]

    def test_fences_stay_paired_across_findings(self) -> None:
        result = CheckResult(
            "a-check",
            "title",
            "spec",
            [
                Finding(Severity.ERROR, "one", "```"),
                Finding(Severity.ERROR, "two", "plain"),
            ],
        )
        text = report_markdown(_report(result))
        assert [_evidence_fences(text, i) for i in range(2)] == [
            ["````", "````"],
            ["```", "```"],
        ]

    def test_finding_without_evidence_opens_no_block(self) -> None:
        text = report_markdown(_report(_failed(evidence=None)))
        assert "<details><summary>evidence</summary>" not in text


class TestNotForTerminals:
    def test_no_ansi_escapes(self) -> None:
        assert "\x1b" not in report_markdown(_report(_failed()))

    def test_is_not_wrapped_to_a_terminal_width(self) -> None:
        """The terminal report wraps to a column width; Markdown is laid out by GitHub,
        so wrapping would be wrong.
        """
        long_line = "x" * 300
        assert long_line in report_markdown(_report(_failed(evidence=long_line)))

    def test_ends_with_exactly_one_newline(self) -> None:
        text = report_markdown(_report(_passed()))
        assert text.endswith("\n")
        assert not text.endswith("\n\n")


class TestLanguages:
    def test_switches_with_the_language(self) -> None:
        report = _report(_failed())
        english = report_markdown(report)
        try:
            i18n.set_language("zh")
            chinese = report_markdown(report)
        finally:
            i18n.set_language("en")
        assert english != chinese
        assert "#### 未通过（1）" in chinese
        assert "规范：" in chinese
        assert "#### Failed" not in chinese

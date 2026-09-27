"""Tests for conformance report rendering.

``build_report()`` returns ``Text`` lines rather than printing, so widths are assertable
without a Console. Passing checks take one line; evidence is flattened and truncated.
"""

from __future__ import annotations

from mcpdump import i18n
from mcpdump.core.session import ServerInfo
from mcpdump.services.checks import CheckResult, Finding, Severity
from mcpdump.services.report import build_report
from mcpdump.services.runner import CheckReport
from mcpdump.ui import display_width

WIDTH = 80


def _report(*results: CheckResult) -> CheckReport:
    return CheckReport(ServerInfo(name="demo", version="1.0"), list(results))


def _passed(check_id: str = "a-check") -> CheckResult:
    return CheckResult(check_id, f"{check_id} title", "spec clause")


def _failed(
    check_id: str = "a-check",
    *,
    message: str = "something is wrong",
    evidence: str | None = "{\"jsonrpc\":\"2.0\"}",
) -> CheckResult:
    return CheckResult(
        check_id,
        f"{check_id} title",
        "spec clause",
        [Finding(Severity.ERROR, message, evidence)],
    )


def _lines(report: CheckReport) -> list[str]:
    return [line.plain for line in build_report(report, width=WIDTH)]


class TestLayout:
    def test_starts_with_title_and_summary(self) -> None:
        lines = _lines(_report(_passed()))
        assert lines[0] == i18n.t("check.title")
        assert "1 checks" in lines[1]

    def test_ends_with_a_verdict(self) -> None:
        assert "All 1 checks passed." in _lines(_report(_passed()))[-1]
        assert "1 of 1 checks failed." in _lines(_report(_failed()))[-1]

    def test_passing_check_takes_exactly_one_line(self) -> None:
        """Expanding passing checks is noise. Title + summary + blank + one line +
        blank + verdict.
        """
        lines = _lines(_report(_passed()))
        assert len(lines) == 6
        assert "a-check" in lines[3]

    def test_passing_check_hides_the_spec_clause(self) -> None:
        assert not any("spec" in line for line in _lines(_report(_passed())))

    def test_failing_check_shows_spec_message_and_evidence(self) -> None:
        lines = _lines(_report(_failed()))
        body = "\n".join(lines)
        assert "spec clause" in body
        assert "something is wrong" in body
        assert "evidence:" in body

    def test_skipped_check_explains_why(self) -> None:
        result = CheckResult("a-check", "title", "spec", skipped="no tools capability")
        lines = _lines(_report(result))
        assert any("no tools capability" in line for line in lines)

    def test_warning_count_is_surfaced(self) -> None:
        result = CheckResult(
            "a-check", "title", "spec", [Finding(Severity.WARNING, "heads up", "ev")]
        )
        lines = _lines(_report(result))
        assert any("Warnings: 1" in line for line in lines)

    def test_no_warning_line_when_there_are_none(self) -> None:
        assert not any("Warnings" in line for line in _lines(_report(_passed())))


class TestEvidenceTruncation:
    def test_long_evidence_is_clipped_to_the_width(self) -> None:
        evidence = "x" * 5000
        report = _report(_failed(evidence=evidence))
        for line in build_report(report, width=WIDTH):
            assert display_width(line.plain) <= WIDTH

    def test_multiline_evidence_is_flattened_to_one_line(self) -> None:
        report = _report(_failed(evidence="first\nsecond\nthird"))
        evidence_lines = [line for line in _lines(report) if "evidence:" in line]
        assert len(evidence_lines) == 1
        assert "first second third" in evidence_lines[0]

    def test_no_evidence_line_when_finding_has_none(self) -> None:
        report = _report(_failed(evidence=None))
        assert not any("evidence:" in line for line in _lines(report))


class TestLanguages:
    def test_switches_with_the_language(self) -> None:
        report = _report(_failed())
        english = _lines(report)
        try:
            i18n.set_language("zh")
            chinese = _lines(report)
        finally:
            i18n.set_language("en")
        assert english != chinese
        assert "一致性报告" in chinese[0]
        assert "规范：" in "\n".join(chinese)

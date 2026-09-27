"""Tests for the conformance checks.

With no fault enabled the fixture and the demo server pass all 10 checks; injecting one
fault must fail exactly the targeted check, proving the checks do not overlap.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

import pytest

from conftest import ServerCommand
from mcpdump.runtime import SessionOptions
from mcpdump.services.checks import CheckResult, Severity, Status
from mcpdump.services.checks.base import RawProbe
from mcpdump.services.runner import CheckReport

#: Fault name -> the check id it should hit.
#: undeclared-capability is absent here: it warns rather than fails, so it is tested apart.
FAULT_TO_CHECK: dict[str, str] = {
    "no-protocol-version": "handshake-protocol-version",
    "request-before-initialized": "handshake-initialized-order",
    "schema-not-object": "schema-valid-json-schema",
    "required-unknown-name": "schema-required-exists",
    "unknown-tool-wrong-code": "error-unknown-tool-code",
    "invalid-params-succeeds": "error-invalid-params-code",
    "content-not-array": "result-content-array",
    "is-error-not-bool": "result-is-error-flag",
    "stdout-banner": "stdout-purity",
}


def _by_id(report: CheckReport) -> dict[str, CheckResult]:
    return {result.id: result for result in report.results}


def _failed_ids(report: CheckReport) -> set[str]:
    return {result.id for result in report.results if result.status is Status.FAILED}


class TestCompliantBaseline:
    def test_fixture_without_faults_passes_everything(
        self, broken_server: ServerCommand, run_check: Callable[..., CheckReport]
    ) -> None:
        """The fixture's own compliant baseline. If this goes red, the fixture is
        broken, not the code under test.
        """
        report = run_check(broken_server("none"))
        assert _failed_ids(report) == set()
        assert report.score == "10/10"
        assert report.conformant

    def test_echo_server_passes_everything(
        self, echo_server_cmd: str, run_check: Callable[..., CheckReport]
    ) -> None:
        """The bundled example server must pass every check."""
        report = run_check(echo_server_cmd)
        assert _failed_ids(report) == set()
        assert report.total == 10

    def test_no_check_is_skipped_on_a_full_server(
        self, echo_server_cmd: str, run_check: Callable[..., CheckReport]
    ) -> None:
        report = run_check(echo_server_cmd)
        assert report.skipped == 0


class TestFaultDetection:
    @pytest.mark.parametrize("fault,check_id", sorted(FAULT_TO_CHECK.items()))
    def test_fault_fails_its_own_check_only(
        self,
        broken_server: ServerCommand,
        run_check: Callable[..., CheckReport],
        fault: str,
        check_id: str,
    ) -> None:
        report = run_check(broken_server(fault))
        assert _failed_ids(report) == {check_id}, (
            f"{fault} must fail only {check_id}; the failures were {_failed_ids(report)}"
        )

    def test_all_faults_together_are_all_reported(
        self, broken_server: ServerCommand, run_check: Callable[..., CheckReport]
    ) -> None:
        report = run_check(broken_server("all"))
        assert _failed_ids(report) == set(FAULT_TO_CHECK.values())

    def test_undeclared_capability_is_a_warning_not_a_failure(
        self, broken_server: ServerCommand, run_check: Callable[..., CheckReport]
    ) -> None:
        """Reachable but undeclared: a client skips it for nothing, yet the server
        breaks no rule.
        """
        report = run_check(broken_server("undeclared-capability"))
        result = _by_id(report)["capability-method-consistency"]
        assert result.status is Status.PASSED
        assert any(item.severity is Severity.WARNING for item in result.findings)
        assert report.warnings == 1

    def test_unknown_tool_uses_the_right_probe_name(
        self, broken_server: ServerCommand, run_check: Callable[..., CheckReport]
    ) -> None:
        """A fixed probe name is what makes reports reproducible and comparable."""
        report = run_check(broken_server("unknown-tool-wrong-code"))
        result = _by_id(report)["error-unknown-tool-code"]
        assert any("-32601" in item.message for item in result.findings)


class TestEvidence:
    def test_every_error_finding_carries_evidence(
        self, broken_server: ServerCommand, run_check: Callable[..., CheckReport]
    ) -> None:
        """Every failure carries evidence. A report without evidence is just an assertion."""
        report = run_check(broken_server("all"))
        for result in report.results:
            for item in result.findings:
                if item.severity is Severity.ERROR:
                    assert item.evidence, f"{result.id} carries an ERROR with no evidence"

    def test_failed_check_keeps_its_spec_clause(
        self, broken_server: ServerCommand, run_check: Callable[..., CheckReport]
    ) -> None:
        """A failed check must carry its spec clause, so the user can verify it rather
        than take our word for it.
        """
        report = run_check(broken_server("all"))
        for result in report.results:
            if result.status is Status.FAILED:
                assert result.spec.strip(), f"{result.id} is missing its spec"


class TestInvalidParamsProbesEveryTool:
    def test_every_tool_with_required_is_probed(
        self, broken_server: ServerCommand, run_check: Callable[..., CheckReport]
    ) -> None:
        """Probing only the first tool misses later defects of the same kind, one fix per run."""
        report = run_check(broken_server("invalid-params-succeeds,required-unknown-name"))
        result = _by_id(report)["error-invalid-params-code"]
        errors = [item for item in result.findings if item.severity is Severity.ERROR]
        assert len(errors) == 2
        assert "needs-arg" in errors[0].message
        assert "bad-required" in errors[1].message


class TestReportShape:
    def test_to_dict_is_stable_and_machine_readable(
        self, broken_server: ServerCommand, run_check: Callable[..., CheckReport]
    ) -> None:
        report = run_check(broken_server("stdout-banner"))
        payload = report.to_dict()
        assert payload["summary"]["score"] == "9/10"
        assert payload["summary"]["conformant"] is False
        assert payload["summary"]["failed"] == 1
        entry = next(item for item in payload["checks"] if item["id"] == "stdout-purity")
        assert entry["status"] == "failed"
        assert entry["findings"][0]["evidence"]

    def test_skipped_reason_reaches_the_machine_channel(
        self, tools_only_server_cmd: str, run_check: Callable[..., CheckReport]
    ) -> None:
        """On a tools-only server, checks that need resources/prompts should skip and say why."""
        report = run_check(tools_only_server_cmd)
        skipped = [item for item in report.to_dict()["checks"] if item["status"] == "skipped"]
        assert skipped, "a tools-only server should not have zero skipped checks"
        assert all(item["skippedReason"] for item in skipped)


def test_raw_probe_honours_argv_over_the_display_string() -> None:
    """A raw connection must go through ``launch_spec`` (``argv`` wins), not ``server``.

    Falling back to ``server`` splits a path containing spaces a second time and fails at
    ``start()``, so ``server`` is deliberately set to a command that cannot exist.
    """
    opts = SessionOptions(
        server="a-command-that-cannot-possibly-exist",
        argv=[sys.executable, "-m", "mcpdump.demo"],
        timeout=30.0,
        show_server_stderr=False,
    )
    with RawProbe(opts) as probe:
        assert probe.unsolicited == []

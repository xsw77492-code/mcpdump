"""Runner for the conformance checks.

A single check must never take the whole run down: expected failures become
findings inside the check, unexpected exceptions are caught here and recorded as
a failure carrying the exception text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core import MCPSession
from ..core.session import ServerInfo
from ..i18n import t
from .checks import (
    Check,
    CheckContext,
    CheckResult,
    Finding,
    Severity,
    Status,
    all_checks,
)

if TYPE_CHECKING:
    # As in ``checks/base.py``: ``runtime`` sits above ``services``, so importing
    # it at runtime would be a cycle.
    from ..runtime import SessionOptions

__all__ = ["CheckReport", "run_checks"]


@dataclass
class CheckReport:
    """The result of one full run. ``to_dict()`` is the machine-readable channel;
    rendering is another.
    """

    server: ServerInfo
    results: list[CheckResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(result.status is Status.PASSED for result in self.results)

    @property
    def failed(self) -> int:
        return sum(result.status is Status.FAILED for result in self.results)

    @property
    def skipped(self) -> int:
        return sum(result.status is Status.SKIPPED for result in self.results)

    @property
    def warnings(self) -> int:
        return sum(
            1
            for result in self.results
            for finding in result.findings
            if finding.severity is Severity.WARNING
        )

    @property
    def score(self) -> str:
        """The grade as ``10/10``, used by both the badge and the summary."""
        return f"{self.passed}/{self.total}"

    @property
    def conformant(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": {
                "name": self.server.name,
                "version": self.server.version,
                "protocolVersion": self.server.protocol_version,
                "capabilities": sorted(self.server.capabilities),
            },
            "summary": {
                "total": self.total,
                "passed": self.passed,
                "failed": self.failed,
                "skipped": self.skipped,
                "warnings": self.warnings,
                "score": self.score,
                "conformant": self.conformant,
            },
            "checks": [result.to_dict() for result in self.results],
        }


def run_checks(
    opts: SessionOptions,
    session: MCPSession,
    *,
    checks: tuple[Check, ...] | None = None,
    probe_timeout: float = 0.3,
) -> CheckReport:
    """Run every check, or just those passed in ``checks``."""
    ctx = CheckContext(opts, session, probe_timeout=probe_timeout)
    selected = all_checks() if checks is None else checks
    return CheckReport(session.server, [_run_one(check, ctx) for check in selected])


def _run_one(check: Check, ctx: CheckContext) -> CheckResult:
    try:
        return check.run(ctx)
    except Exception as exc:
        # Every exception is caught on purpose: a broken check must not take the
        # others down with it. The exception text becomes evidence for an issue.
        return CheckResult(
            id=check.id,
            title=check.title(),
            spec=check.spec,
            findings=[Finding(
                severity=Severity.ERROR,
                message=t("check.finding.crashed", error=f"{type(exc).__name__}: {exc}"),
            )],
        )

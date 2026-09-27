"""Check 3: a declared capability must be backed by its method.

Both directions matter:

- Declared but not callable: a client following the declaration hits
  METHOD_NOT_FOUND. That is a hard violation.
- Callable but not declared: capability gating makes clients skip it, hiding a
  working feature. Worth a warning, not a violation.
"""

from __future__ import annotations

from ...core.jsonrpc import METHOD_NOT_FOUND, JsonRpcError
from ...i18n import t
from .base import BaseCheck, CheckContext, CheckResult, Finding, error, warn

#: Capabilities with a list method. ``logging`` and ``completions`` have none,
#: so they cannot be probed.
CAPABILITY_METHODS: dict[str, str] = {
    "tools": "tools/list",
    "resources": "resources/list",
    "prompts": "prompts/list",
}

#: Transport-level failures: no answer at all, rather than an error code.
TRANSPORT_ERRORS = (TimeoutError, RuntimeError)


class CapabilityMethodCheck(BaseCheck):
    id = "capability-method-consistency"
    spec = "MCP lifecycle: capabilities declared in initialize must be backed by their methods"

    def run(self, ctx: CheckContext) -> CheckResult:
        declared = ctx.server.capabilities
        findings: list[Finding] = []
        for capability, method in CAPABILITY_METHODS.items():
            findings.extend(self._probe(ctx, capability, method, declared=capability in declared))
        return self.result(*findings)

    def _probe(
        self, ctx: CheckContext, capability: str, method: str, *, declared: bool
    ) -> list[Finding]:
        try:
            ctx.request(method)
        except JsonRpcError as exc:
            if declared:
                return [error(
                    t(
                        "check.capability_method_consistency.unimplemented",
                        capability=capability,
                        method=method,
                        error=f"[{exc.code}] {exc.message}",
                    ),
                    ctx.evidence_of(method),
                )]
            if exc.code == METHOD_NOT_FOUND:
                return []  # Not declared and not implemented: correct.
            return [warn(
                t(
                    "check.capability_method_consistency.undeclared_error",
                    capability=capability,
                    method=method,
                    error=f"[{exc.code}] {exc.message}",
                ),
                ctx.evidence_of(method),
            )]
        except TRANSPORT_ERRORS as exc:
            return [error(
                t("check.capability_method_consistency.no_response", method=method, error=exc),
                ctx.evidence_of(method),
            )]

        if declared:
            return []
        return [warn(
            t(
                "check.capability_method_consistency.undeclared",
                capability=capability,
                method=method,
            ),
            ctx.evidence_of(method),
        )]


CHECKS = (CapabilityMethodCheck(),)

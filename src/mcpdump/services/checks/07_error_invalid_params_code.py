"""Check 7: missing required arguments must return ``-32602``, not run anyway.

Three outcomes are separated by severity: ``-32602`` is conformant; a result with
``isError: true`` is tolerable but warned; a straight success means the request
ran on defaults while the caller believes the arguments took effect.

Every tool with required parameters is probed, since hand-written servers often
validate tool by tool. The probe sends an empty object rather than a fabricated
field name, because some servers reject unknown fields and that would pollute the
conclusion.
"""

from __future__ import annotations

from typing import Any

from ...core.jsonrpc import INVALID_PARAMS, JsonRpcError
from ...i18n import t
from .base import BaseCheck, CheckContext, CheckResult, Finding, error, note, warn

TRANSPORT_ERRORS = (TimeoutError, RuntimeError)

#: Cap on probed tools. Beyond it only the first N are checked: a server with
#: hundreds of tools should not receive hundreds of calls, which is slow and may
#: trigger side effects.
MAX_PROBED_TOOLS = 10


class InvalidParamsCodeCheck(BaseCheck):
    id = "error-invalid-params-code"
    spec = "MCP tools: missing required arguments map to -32602 Invalid params"

    def run(self, ctx: CheckContext) -> CheckResult:
        if not ctx.server.supports("tools"):
            return self.skip(t("check.skip.no_tools"))

        targets = self._with_required(ctx)
        if not targets:
            return self.skip(t("check.skip.no_required_tool"))

        findings: list[Finding] = []
        for tool in targets[:MAX_PROBED_TOOLS]:
            findings.extend(self._probe(ctx, str(tool.get("name", "?"))))
        if len(targets) > MAX_PROBED_TOOLS:
            findings.append(note(t(
                "check.error_invalid_params_code.truncated",
                limit=MAX_PROBED_TOOLS,
                total=len(targets),
            )))
        return self.result(*findings)

    def _probe(self, ctx: CheckContext, name: str) -> list[Finding]:
        """Send one empty-argument call to a single tool; empty list when conformant."""
        try:
            result = ctx.request("tools/call", {"name": name, "arguments": {}})
        except JsonRpcError as exc:
            if exc.code == INVALID_PARAMS:
                return []
            return [error(
                t(
                    "check.error_invalid_params_code.wrong_code",
                    tool=name,
                    code=exc.code,
                    expected=INVALID_PARAMS,
                ),
                ctx.evidence(),
            )]
        except TRANSPORT_ERRORS as exc:
            return [error(t("check.finding.no_response", error=exc), ctx.evidence())]

        if isinstance(result, dict) and result.get("isError") is True:
            return [warn(
                t("check.error_invalid_params_code.is_error_flag", tool=name), ctx.evidence()
            )]
        return [error(
            t("check.error_invalid_params_code.succeeded", tool=name), ctx.evidence()
        )]

    def _with_required(self, ctx: CheckContext) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for tool in ctx.tools():
            schema = tool.get("inputSchema")
            if isinstance(schema, dict) and schema.get("required"):
                out.append(tool)
        return out


CHECKS = (InvalidParamsCodeCheck(),)

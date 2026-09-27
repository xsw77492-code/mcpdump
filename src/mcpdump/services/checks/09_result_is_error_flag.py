"""Check 9: does a ``tools/call`` result state ``isError`` explicitly?

MCP makes ``isError`` optional and defaults it to false, so omitting it is
unclear rather than non-conformant: a warning, not an error. It is still worth
checking, because callers use the field to tell "the tool failed" from "the tool
returned text describing a failure".
"""

from __future__ import annotations

from ...i18n import t
from .base import BaseCheck, CheckContext, CheckResult, error, warn


class ResultIsErrorFlagCheck(BaseCheck):
    id = "result-is-error-flag"
    spec = "MCP tools: results should state isError so callers need not guess"

    def run(self, ctx: CheckContext) -> CheckResult:
        if not ctx.server.supports("tools"):
            return self.skip(t("check.skip.no_tools"))
        sample = ctx.smoke_call()
        if sample is None:
            return self.skip(t("check.skip.no_smoke_tool"))

        result, name = sample
        evidence = ctx.evidence_of("tools/call")
        if "isError" not in result:
            return self.result(warn(
                t("check.result_is_error_flag.missing", tool=name), evidence
            ))

        value = result["isError"]
        if not isinstance(value, bool):
            return self.result(error(
                t(
                    "check.result_is_error_flag.not_bool",
                    tool=name,
                    kind=type(value).__name__,
                ),
                evidence,
            ))
        return self.result()


CHECKS = (ResultIsErrorFlagCheck(),)

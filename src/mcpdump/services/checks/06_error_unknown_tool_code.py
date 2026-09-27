"""Check 6: calling an unknown tool must return ``-32602``.

The spec files an unknown tool under Invalid params (``-32602``), not Method not
found (``-32601``) — the latter means the ``tools/call`` method itself does not
exist. A client writing retry logic against that convention is misled by the
wrong code.

The probe name carries an ``mcpdump`` prefix, so a collision with a real tool is
unlikely; if one happens, the check skips rather than reporting a false positive.
"""

from __future__ import annotations

from ...core.jsonrpc import INVALID_PARAMS, JsonRpcError
from ...i18n import t
from .base import BaseCheck, CheckContext, CheckResult, error

#: Probe tool name. A fixed string rather than a random one, so reports and
#: reproduction steps stay comparable.
PROBE_TOOL = "mcpdump-conformance-probe-does-not-exist"

TRANSPORT_ERRORS = (TimeoutError, RuntimeError)


class UnknownToolCodeCheck(BaseCheck):
    id = "error-unknown-tool-code"
    spec = "MCP tools: an unknown tool name maps to -32602 Invalid params"

    def run(self, ctx: CheckContext) -> CheckResult:
        if not ctx.server.supports("tools"):
            return self.skip(t("check.skip.no_tools"))
        if PROBE_TOOL in ctx.tool_names():
            return self.skip(t("check.skip.probe_name_taken", name=PROBE_TOOL))

        try:
            ctx.request("tools/call", {"name": PROBE_TOOL, "arguments": {}})
        except JsonRpcError as exc:
            if exc.code == INVALID_PARAMS:
                return self.result()
            return self.result(error(
                t(
                    "check.error_unknown_tool_code.wrong_code",
                    code=exc.code,
                    expected=INVALID_PARAMS,
                ),
                ctx.evidence(),
            ))
        except TRANSPORT_ERRORS as exc:
            return self.result(error(t("check.finding.no_response", error=exc), ctx.evidence()))

        # No error, so the server treated a non-existent tool as a successful call.
        return self.result(error(
            t("check.error_unknown_tool_code.succeeded", tool=PROBE_TOOL), ctx.evidence()
        ))


CHECKS = (UnknownToolCodeCheck(),)

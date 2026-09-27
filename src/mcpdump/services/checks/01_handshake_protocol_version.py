"""Check 1: does the initialize result carry a protocolVersion?

First because every later check depends on it: without the negotiated version,
each subsequent step is guessing which revision the server speaks.
"""

from __future__ import annotations

from ...i18n import t
from .base import BaseCheck, CheckContext, CheckResult, error, note


class ProtocolVersionCheck(BaseCheck):
    id = "handshake-protocol-version"
    spec = "MCP lifecycle: the initialize result MUST include protocolVersion"

    def run(self, ctx: CheckContext) -> CheckResult:
        exchange = ctx.session.last_exchange("initialize")
        if exchange is None:
            return self.skip(t("check.skip.no_initialize"))
        raw = ctx.server.raw or {}
        evidence = exchange.response_line

        if "protocolVersion" not in raw:
            return self.result(
                error(t("check.handshake_protocol_version.missing"), evidence)
            )

        value = raw.get("protocolVersion")
        if not isinstance(value, str) or not value:
            return self.result(error(t("check.handshake_protocol_version.empty"), evidence))

        offered = ctx.opts.protocol_version
        if value != offered:
            # Version negotiation lets the server answer with a version it
            # supports, so this is an observation rather than a violation.
            return self.result(note(
                t("check.handshake_protocol_version.mismatch", actual=value, offered=offered),
                evidence,
            ))
        return self.result()


CHECKS = (ProtocolVersionCheck(),)

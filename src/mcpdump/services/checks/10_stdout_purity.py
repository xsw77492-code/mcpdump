"""Check 10: nothing but JSON-RPC frames may appear on stdout.

This has to run last, which is what the ``10`` in the filename is for: it reads
the non-protocol output accumulated over the whole session. Run earlier it would
only see the handshake, missing any logs written later — a false pass.

A banner on stdout is the classic way an MCP server breaks: the client fails to
parse it as a frame and the connection dies for no visible reason.
"""

from __future__ import annotations

from ...i18n import t
from .base import BaseCheck, CheckContext, CheckResult, error


class StdoutPurityCheck(BaseCheck):
    id = "stdout-purity"
    spec = "MCP stdio transport: the server MUST write only JSON-RPC frames to stdout"

    def run(self, ctx: CheckContext) -> CheckResult:
        lines = ctx.session.non_protocol_output
        if not lines:
            return self.result()
        return self.result(error(
            t("check.stdout_purity.violation", count=len(lines)),
            "\n".join(lines),
        ))


CHECKS = (StdoutPurityCheck(),)

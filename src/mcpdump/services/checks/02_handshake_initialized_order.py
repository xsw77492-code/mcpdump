"""Check 2: the server must not send requests before ``notifications/initialized``.

This cannot use an already-handshaken session: ``MCPSession`` sends the
``initialized`` notification the moment ``initialize`` returns, closing the
observation window. So the handshake is driven by hand over a raw connection.
"""

from __future__ import annotations

from typing import Any

from ...core import initialize_params
from ...core.jsonrpc import is_notification, is_request
from ...i18n import t
from .base import BaseCheck, CheckContext, CheckResult, error, json_evidence, warn

#: Notifications allowed before initialized; log messages are explicitly permitted.
ALLOWED_EARLY_NOTIFICATIONS = frozenset({"notifications/message"})


class InitializedOrderCheck(BaseCheck):
    id = "handshake-initialized-order"
    spec = "MCP lifecycle: the server SHOULD NOT send requests before notifications/initialized"

    def run(self, ctx: CheckContext) -> CheckResult:
        with ctx.open_raw() as probe:
            msg_id = probe.send_request(
                "initialize", initialize_params(ctx.opts.protocol_version)
            )
            probe.await_response(msg_id, ctx.opts.timeout)
            inbound = [*probe.unsolicited, *probe.drain(ctx.probe_timeout)]
            # Complete the handshake before closing, so the server does not
            # record an abrupt disconnect.
            probe.send_notification("notifications/initialized")

        findings = [self._judge(msg) for msg in inbound]
        return self.result(*(item for item in findings if item is not None))

    def _judge(self, msg: dict[str, Any]) -> Any:
        method = str(msg.get("method", "?"))
        if is_request(msg):
            return error(
                t("check.handshake_initialized_order.request", method=method),
                json_evidence(msg),
            )
        if is_notification(msg) and method not in ALLOWED_EARLY_NOTIFICATIONS:
            return warn(
                t("check.handshake_initialized_order.notification", method=method),
                json_evidence(msg),
            )
        return None


CHECKS = (InitializedOrderCheck(),)

"""Check 5: every name in ``required`` must exist in ``properties``.

The commonest JSON Schema slip: a field is renamed and ``required`` is not
updated. A model generating arguments from the schema then always omits a
required field, and the error it gets back rarely points at the cause.
"""

from __future__ import annotations

from typing import Any

from ...i18n import t
from .base import BaseCheck, CheckContext, CheckResult, Finding, error, json_evidence


class RequiredExistsCheck(BaseCheck):
    id = "schema-required-exists"
    spec = "MCP tools: every name in inputSchema.required must be declared in properties"

    def run(self, ctx: CheckContext) -> CheckResult:
        if not ctx.server.supports("tools"):
            return self.skip(t("check.skip.no_tools"))
        findings: list[Finding] = []
        for tool in ctx.tools():
            findings.extend(self._inspect(str(tool.get("name", "?")), tool))
        return self.result(*findings)

    def _inspect(self, name: str, tool: dict[str, Any]) -> list[Finding]:
        schema = tool.get("inputSchema")
        # Whether the schema is structurally sound is the previous check's job;
        # this one looks only at mismatched names, so the same fault is not
        # reported twice.
        if not isinstance(schema, dict):
            return []
        required = schema.get("required")
        properties = schema.get("properties")
        if not isinstance(required, list) or not isinstance(properties, dict):
            return []

        missing = [item for item in required if isinstance(item, str) and item not in properties]
        if not missing:
            return []
        return [error(
            t(
                "check.schema_required_exists.missing",
                tool=name,
                names=t("list.separator").join(repr(item) for item in missing),
            ),
            json_evidence(schema),
        )]


CHECKS = (RequiredExistsCheck(),)

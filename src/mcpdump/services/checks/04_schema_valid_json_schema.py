"""Check 4: is each tool's ``inputSchema`` a plausible JSON Schema?

No JSON Schema library is used, since the runtime dependencies are only typer
and rich. This is a structural check covering the mistakes that actually occur,
not a full implementation.
"""

from __future__ import annotations

from typing import Any

from ...i18n import t
from .base import BaseCheck, CheckContext, CheckResult, Finding, error, json_evidence, warn

#: MCP defines inputSchema as describing an object, so ``type`` must literally
#: be object.
REQUIRED_TYPE = "object"


class SchemaShapeCheck(BaseCheck):
    id = "schema-valid-json-schema"
    spec = "MCP tools: inputSchema MUST be a JSON Schema object describing the arguments"

    def run(self, ctx: CheckContext) -> CheckResult:
        if not ctx.server.supports("tools"):
            return self.skip(t("check.skip.no_tools"))
        findings: list[Finding] = []
        for tool in ctx.tools():
            findings.extend(self._inspect(str(tool.get("name", "?")), tool))
        return self.result(*findings)

    def _inspect(self, name: str, tool: dict[str, Any]) -> list[Finding]:
        if "inputSchema" not in tool:
            return [error(
                t("check.schema_valid_json_schema.missing", tool=name), json_evidence(tool)
            )]

        schema = tool["inputSchema"]
        if not isinstance(schema, dict):
            return [error(
                t(
                    "check.schema_valid_json_schema.not_object",
                    tool=name,
                    kind=type(schema).__name__,
                ),
                json_evidence(tool),
            )]

        findings: list[Finding] = []
        declared = schema.get("type")
        if declared is None:
            findings.append(warn(
                t("check.schema_valid_json_schema.missing_type", tool=name),
                json_evidence(schema),
            ))
        elif declared != REQUIRED_TYPE:
            findings.append(error(
                t("check.schema_valid_json_schema.bad_type", tool=name, value=declared),
                json_evidence(schema),
            ))

        properties = schema.get("properties")
        if properties is not None and (
            not isinstance(properties, dict)
            or any(not isinstance(spec, dict) for spec in properties.values())
        ):
            findings.append(error(
                t("check.schema_valid_json_schema.bad_properties", tool=name),
                json_evidence(schema),
            ))

        required = schema.get("required")
        if required is not None and (
            not isinstance(required, list)
            or any(not isinstance(item, str) for item in required)
        ):
            findings.append(error(
                t("check.schema_valid_json_schema.bad_required", tool=name),
                json_evidence(schema),
            ))
        return findings


CHECKS = (SchemaShapeCheck(),)

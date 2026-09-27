"""Check 8: does a ``tools/call`` result carry a valid ``content`` array?

The sample comes from ``ctx.smoke_call()``, which really calls a tool that needs
no arguments. Better than a fabricated call: it also shows the server works on
the normal path.
"""

from __future__ import annotations

from typing import Any

from ...i18n import t
from .base import BaseCheck, CheckContext, CheckResult, Finding, error, json_evidence

#: Content block types defined by MCP.
BLOCK_TYPES = frozenset({"text", "image", "audio", "resource", "resource_link"})

#: Types that must carry both base64 ``data`` and ``mimeType``.
BLOB_TYPES = frozenset({"image", "audio"})


class ResultContentArrayCheck(BaseCheck):
    id = "result-content-array"
    spec = "MCP tools: a tools/call result MUST carry a content array of valid blocks"

    def run(self, ctx: CheckContext) -> CheckResult:
        if not ctx.server.supports("tools"):
            return self.skip(t("check.skip.no_tools"))
        sample = ctx.smoke_call()
        if sample is None:
            return self.skip(t("check.skip.no_smoke_tool"))

        result, name = sample
        evidence = ctx.evidence_of("tools/call")
        if "content" not in result:
            return self.result(error(
                t("check.result_content_array.missing", tool=name), evidence
            ))

        content = result["content"]
        if not isinstance(content, list):
            return self.result(error(
                t(
                    "check.result_content_array.not_array",
                    tool=name,
                    kind=type(content).__name__,
                ),
                evidence,
            ))

        findings: list[Finding] = []
        for index, block in enumerate(content):
            findings.extend(self._inspect_block(name, index, block))
        return self.result(*findings)

    def _inspect_block(self, tool: str, index: int, block: Any) -> list[Finding]:
        if not isinstance(block, dict):
            return [self._bad(tool, index, type(block).__name__, block)]

        kind = block.get("type")
        if kind not in BLOCK_TYPES:
            return [self._bad(tool, index, kind, block)]

        if kind == "text" and not isinstance(block.get("text"), str):
            return [error(
                t("check.result_content_array.bad_text", tool=tool, index=index),
                json_evidence(block),
            )]

        if kind in BLOB_TYPES and not (
            isinstance(block.get("data"), str) and isinstance(block.get("mimeType"), str)
        ):
            return [error(
                t(
                    "check.result_content_array.bad_blob",
                    tool=tool,
                    index=index,
                    value=kind,
                ),
                json_evidence(block),
            )]

        if kind == "resource" and not isinstance(block.get("resource"), dict):
            return [error(
                t("check.result_content_array.bad_resource", tool=tool, index=index),
                json_evidence(block),
            )]
        return []

    def _bad(self, tool: str, index: int, value: Any, block: Any) -> Finding:
        return error(
            t(
                "check.result_content_array.bad_block",
                tool=tool,
                index=index,
                value=value,
            ),
            json_evidence(block),
        )


CHECKS = (ResultContentArrayCheck(),)

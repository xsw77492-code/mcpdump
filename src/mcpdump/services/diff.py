"""Structured differences between two session recordings.

Not a line-by-line comparison of the two JSONL files: the recordings carry
``seq``, ``atMs`` and ``elapsedMs``, which differ every run, so a text diff
reports the whole file as changed and buries the real difference.

Prose, time and line numbers are deliberately left out: what is compared is
tools added or removed, a tool's contract changing, declared capabilities
changing, and latency regressing behind a two-sided threshold.

The threshold is two-sided because a fixed millisecond value fails both ways:
30 ms of jitter on a 50 ms call is nothing, and 30 ms on a 3 s call is not worth
reporting either.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..i18n import t
from .recorder import RecordedFrame, message_id, message_method

__all__ = [
    "CHANGE_CAPABILITY",
    "CHANGE_LATENCY",
    "CHANGE_TOOL_ADDED",
    "CHANGE_TOOL_REMOVED",
    "CHANGE_TOOL_SCHEMA",
    "LATENCY_MIN_DELTA_MS",
    "LATENCY_RATIO",
    "Change",
    "Contract",
    "ContractDiff",
    "ToolContract",
    "build_contract",
    "diff_contracts",
    "summarize_contract",
]

CHANGE_TOOL_ADDED = "tool_added"
CHANGE_TOOL_REMOVED = "tool_removed"
CHANGE_TOOL_SCHEMA = "tool_schema"
CHANGE_CAPABILITY = "capability"
CHANGE_LATENCY = "latency"

#: Latency regression threshold: both a relative increase and an absolute delta
#: must hold. 1.5x means 800 ms to 1200 ms is the point of interest, and the 50 ms
#: floor filters percentage noise on small calls.
LATENCY_RATIO = 1.5
LATENCY_MIN_DELTA_MS = 50.0

#: Only these keys count in a contract comparison. ``description`` is deliberately
#: absent; see the module docstring.
_SCHEMA_CONTRACT_KEYS = ("type", "properties", "required", "items", "enum", "additionalProperties")

#: Keys whose value maps names to sub-schemas (``{"text": {...}}``). The names
#: carry meaning, so the whole mapping is carried through rather than filtered.
#:
#: Filtering them away makes ``properties`` an empty dict, which silently hides
#: every parameter added, removed, renamed or retyped.
_NAME_KEYED_KEYS = ("properties", "patternProperties", "definitions", "$defs")


@dataclass(frozen=True)
class Change:
    """One difference. ``detail`` is a sentence for a person; ``subject`` is the
    stable identifier for a program.
    """

    kind: str
    subject: str
    detail: str
    before: Any = None
    after: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "detail": self.detail,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True)
class ToolContract:
    """A tool as its contract sees it."""

    name: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "inputSchema": self.input_schema,
            "outputSchema": self.output_schema,
        }


@dataclass
class Contract:
    """A snapshot of the server's contract as seen in one recording.

    The last declaration wins: ``tools/list`` may appear several times in one
    recording (client retries, mcpdump's own probes), and mixing those in would
    make the diff depend on how many times a call happened to be made.
    """

    server_name: str = ""
    server_version: str = ""
    protocol_version: str = ""
    capabilities: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, ToolContract] = field(default_factory=dict)
    latencies: dict[str, list[float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": {"name": self.server_name, "version": self.server_version},
            "protocolVersion": self.protocol_version,
            "capabilities": sorted(self.capabilities),
            "tools": {name: tool.to_dict() for name, tool in sorted(self.tools.items())},
            "latency": {
                method: {"calls": len(values), "medianMs": _median(values)}
                for method, values in sorted(self.latencies.items())
            },
        }


@dataclass
class ContractDiff:
    """The diff result. ``changes`` is deterministically ordered for a given pair
    of inputs.
    """

    before: Contract = field(default_factory=Contract)
    after: Contract = field(default_factory=Contract)
    changes: list[Change] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not self.changes

    def to_dict(self) -> dict[str, Any]:
        return {
            "identical": self.identical,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "changes": [change.to_dict() for change in self.changes],
        }


def _median(values: Sequence[float]) -> float:
    """The median, not the mean: one 30-second timeout raises the mean tenfold,
    which is where a regression test is most easily misled by a single outlier.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _schema_contract(schema: Any) -> Any:
    """Reduce a schema to its contract keys, dropping prose.

    Recursive rather than top-level only: a change confined to the description
    under ``properties.text`` would otherwise look like a contract change.
    Name-keyed keys are handled differently (see ``_NAME_KEYED_KEYS``).
    """
    if isinstance(schema, dict):
        out: dict[str, Any] = {}
        for key in _SCHEMA_CONTRACT_KEYS:
            if key not in schema:
                continue
            value = schema[key]
            if key in _NAME_KEYED_KEYS and isinstance(value, dict):
                out[key] = {name: _schema_contract(sub) for name, sub in value.items()}
            else:
                out[key] = _schema_contract(value)
        # The order of an array-form ``required`` carries no meaning.
        if isinstance(out.get("required"), list):
            out["required"] = sorted(str(item) for item in out["required"])
        return out
    if isinstance(schema, list):
        return [_schema_contract(item) for item in schema]
    return schema


def _describe_schema_delta(before: dict[str, Any], after: dict[str, Any]) -> str:
    """Describe the difference between two schemas. Not exhaustive: the aim is to
    name the parameter that changed.
    """
    parts: list[str] = []
    before_props = before.get("properties") or {}
    after_props = after.get("properties") or {}
    added = sorted(set(after_props) - set(before_props))
    removed = sorted(set(before_props) - set(after_props))
    if added:
        parts.append("+" + ",".join(added))
    if removed:
        parts.append("-" + ",".join(removed))

    changed: list[str] = []
    for name in sorted(set(before_props) & set(after_props)):
        if before_props[name] != after_props[name]:
            old = (before_props[name] or {}).get("type", "?")
            new = (after_props[name] or {}).get("type", "?")
            changed.append(f"{name}: {old}→{new}")
    if changed:
        parts.append("; ".join(changed))

    before_req = sorted(before.get("required") or [])
    after_req = sorted(after.get("required") or [])
    if before_req != after_req:
        parts.append(f"required {before_req}→{after_req}")

    if not parts:
        parts.append(t("diff.schema_changed"))
    return t("diff.schema_delta_separator").join(parts)


def build_contract(frames: Iterable[RecordedFrame]) -> Contract:
    """Extract a contract snapshot from a recording.

    Streamed: only the latest tool and capability tables are kept, so a 100 MB
    recording leaves one tool table in memory.

    Durations sit on the ``to_client`` frame; scanning request frames instead
    leaves the latency table empty and silently disables regression detection.
    The method name comes from the request that caused the response, paired by id.
    """
    contract = Contract()
    tools_seen: dict[str, ToolContract] = {}
    #: id -> the request's method, used to tell a response which endpoint it
    #: belongs to.
    pending: dict[Any, str] = {}

    for frame in frames:
        if frame.to_server:
            method = message_method(frame.line)
            key = message_id(frame.line)
            if key is not None and method:
                pending[key] = method
            continue

        # Only what the server sends: the contract is the server's declaration.
        try:
            payload = json.loads(frame.line)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue

        # A response frame: if it carries a duration, record it under the method
        # that caused it.
        if frame.elapsed_ms is not None:
            key = payload.get("id")
            matched = pending.pop(key, None) if key is not None else None
            if matched:
                contract.latencies.setdefault(matched, []).append(frame.elapsed_ms)

        result = payload.get("result")
        if not isinstance(result, dict):
            continue

        server_info = result.get("serverInfo")
        if isinstance(server_info, dict):
            contract.server_name = str(server_info.get("name", contract.server_name))
            contract.server_version = str(server_info.get("version", contract.server_version))
        if isinstance(result.get("protocolVersion"), str):
            contract.protocol_version = result["protocolVersion"]
        if isinstance(result.get("capabilities"), dict):
            contract.capabilities = result["capabilities"]

        raw_tools = result.get("tools")
        if isinstance(raw_tools, list):
            # Replaced wholesale rather than merged: ``tools/list`` returns the
            # full list, and merging would keep removed tools forever.
            tools_seen = {}
            for item in raw_tools:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if not isinstance(name, str) or not name:
                    continue
                schema = item.get("inputSchema")
                output = item.get("outputSchema")
                tools_seen[name] = ToolContract(
                    name=name,
                    input_schema=_schema_contract(schema if isinstance(schema, dict) else {}),
                    output_schema=(
                        _schema_contract(output) if isinstance(output, dict) else None
                    ),
                )

    contract.tools = tools_seen
    return contract


def diff_contracts(before: Contract, after: Contract) -> ContractDiff:
    """Compare two contracts. Order is fixed: tools, then schemas, then
    capabilities, then latency.
    """
    result = ContractDiff(before=before, after=after)

    before_names = set(before.tools)
    after_names = set(after.tools)

    for name in sorted(after_names - before_names):
        result.changes.append(Change(CHANGE_TOOL_ADDED, name, t("diff.tool_added")))
    for name in sorted(before_names - after_names):
        result.changes.append(Change(CHANGE_TOOL_REMOVED, name, t("diff.tool_removed")))

    for name in sorted(before_names & after_names):
        old, new = before.tools[name], after.tools[name]
        if old.input_schema != new.input_schema:
            result.changes.append(
                Change(
                    CHANGE_TOOL_SCHEMA,
                    f"{name}.inputSchema",
                    _describe_schema_delta(old.input_schema, new.input_schema),
                    before=old.input_schema,
                    after=new.input_schema,
                )
            )
        if old.output_schema != new.output_schema:
            result.changes.append(
                Change(
                    CHANGE_TOOL_SCHEMA,
                    f"{name}.outputSchema",
                    t("diff.output_schema_changed"),
                    before=old.output_schema,
                    after=new.output_schema,
                )
            )

    before_caps = set(before.capabilities)
    after_caps = set(after.capabilities)
    for name in sorted(after_caps - before_caps):
        result.changes.append(Change(CHANGE_CAPABILITY, name, t("diff.capability_added")))
    for name in sorted(before_caps - after_caps):
        result.changes.append(
            Change(CHANGE_CAPABILITY, name, t("diff.capability_removed"))
        )

    if before.protocol_version != after.protocol_version:
        result.changes.append(
            Change(
                CHANGE_CAPABILITY,
                "protocolVersion",
                t("diff.protocol_version_changed"),
                before=before.protocol_version,
                after=after.protocol_version,
            )
        )

    # Named for the purpose: reusing ``name`` from the loops above would make
    # ``old`` and ``new`` resolve to a ``ToolContract`` under arithmetic.
    for latency_method in sorted(set(before.latencies) | set(after.latencies)):
        old_ms = _median(before.latencies.get(latency_method, []))
        new_ms = _median(after.latencies.get(latency_method, []))
        if not old_ms or not new_ms:
            continue
        worsened = new_ms >= old_ms * LATENCY_RATIO
        meaningful = new_ms - old_ms >= LATENCY_MIN_DELTA_MS
        if worsened and meaningful:
            result.changes.append(
                Change(
                    CHANGE_LATENCY,
                    latency_method,
                    t("diff.latency_regressed", old_ms=old_ms, new_ms=new_ms),
                    before=round(old_ms, 3),
                    after=round(new_ms, 3),
                )
            )

    return result


def summarize_contract(contract: Contract) -> dict[str, Any]:
    """A one-line summary for the places that want only the conclusion, such as
    the diff header.
    """
    return {
        "server": contract.server_name,
        "version": contract.server_version,
        "tools": len(contract.tools),
        "capabilities": sorted(contract.capabilities),
    }

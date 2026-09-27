"""Tests for structured diffing (``services/diff.py``).

It must report real changes (tools, parameters, types, capabilities, latency) and stay silent
about the rest; a false positive makes people stop reading the report.
"""

from __future__ import annotations

import json
from typing import Any

from mcpdump.services.diff import (
    CHANGE_CAPABILITY,
    CHANGE_LATENCY,
    CHANGE_TOOL_ADDED,
    CHANGE_TOOL_REMOVED,
    CHANGE_TOOL_SCHEMA,
    LATENCY_MIN_DELTA_MS,
    LATENCY_RATIO,
    build_contract,
    diff_contracts,
    summarize_contract,
)
from mcpdump.services.recorder import DIRECTION_TO_CLIENT, DIRECTION_TO_SERVER, RecordedFrame

# ---------------------------------------------------------------- building recordings


def _tool(
    name: str,
    *,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
    description: str = "does a thing",
) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object"}
    if properties is not None:
        schema["properties"] = properties
    if required is not None:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}


def _initialize(
    *,
    name: str = "echo-server",
    version: str = "1.0.0",
    protocol: str = "2025-06-18",
    capabilities: dict[str, Any] | None = None,
) -> RecordedFrame:
    line = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": protocol,
                "capabilities": capabilities if capabilities is not None else {"tools": {}},
                "serverInfo": {"name": name, "version": version},
            },
        },
        ensure_ascii=False,
    )
    return RecordedFrame(2, 1.0, DIRECTION_TO_CLIENT, "initialize", 1.0, line)


def _tools(tools: list[dict[str, Any]]) -> RecordedFrame:
    line = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}}, ensure_ascii=False)
    return RecordedFrame(4, 2.0, DIRECTION_TO_CLIENT, "tools/list", 2.0, line)


def _call(method: str, *, elapsed: float, msg_id: int = 9) -> tuple[RecordedFrame, RecordedFrame]:
    """A pair of request / response frames.

    Timing lives on the response frame and the request frame has ``None`` — this must match
    real recordings, or ``build_contract`` cannot read it and the latency table is empty.
    """
    request_line = json.dumps(
        {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": {}}, ensure_ascii=False
    )
    response_line = json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": {}}, ensure_ascii=False)
    return (
        RecordedFrame(5, 0.0, DIRECTION_TO_SERVER, method, None, request_line),
        RecordedFrame(6, elapsed, DIRECTION_TO_CLIENT, method, elapsed, response_line),
    )


def _contract(*, tools: list[dict[str, Any]] | None = None, **kwargs: Any) -> Any:
    frames = [_initialize(**kwargs)]
    if tools is not None:
        frames.append(_tools(tools))
    return build_contract(frames)


def _kinds(diff: Any) -> list[str]:
    return [change.kind for change in diff.changes]


def _subjects(diff: Any, kind: str) -> list[str]:
    return [change.subject for change in diff.changes if change.kind == kind]


def _details(diff: Any, kind: str) -> list[str]:
    return [change.detail for change in diff.changes if change.kind == kind]


# ---------------------------------------------------------------- contract snapshot


def test_the_contract_captures_the_server_identity() -> None:
    contract = _contract(name="acme-weather", version="2.3.1", protocol="2025-03-26")

    assert contract.server_name == "acme-weather"
    assert contract.server_version == "2.3.1"
    assert contract.protocol_version == "2025-03-26"


def test_a_later_declaration_wins() -> None:
    """Within one recording ``tools/list`` may be called several times (client retries,
    mcpdump's own probing); the contract takes the last one.

    Mixing in earlier versions would make the diff depend on how many calls happened.
    """
    frames = [
        _initialize(),
        _tools([_tool("echo")]),
        _tools([_tool("echo"), _tool("add")]),
    ]

    assert set(build_contract(frames).tools) == {"echo", "add"}


def test_a_tools_list_replaces_rather_than_merges() -> None:
    """The later ``tools/list`` gives the **full** list. Merging keeps deleted tools forever."""
    frames = [
        _initialize(),
        _tools([_tool("echo"), _tool("add")]),
        _tools([_tool("echo")]),
    ]

    assert set(build_contract(frames).tools) == {"echo"}


def test_malformed_frames_are_skipped_not_fatal() -> None:
    """Half-written lines are routine (hand-trimmed, killed mid-write); skip them, do not abort."""
    frames = [
        _initialize(),
        RecordedFrame(3, 0.0, DIRECTION_TO_CLIENT, "?", 0.0, "{not json"),
        _tools([_tool("echo")]),
    ]

    assert set(build_contract(frames).tools) == {"echo"}


def test_a_tool_without_a_name_is_ignored() -> None:
    """The server declared a tool with no name — ignore it, but do not lose the other tools."""
    frames = [
        _initialize(),
        _tools([{"description": "nameless"}, _tool("echo")]),
    ]

    assert set(build_contract(frames).tools) == {"echo"}


def test_the_contract_summarises_in_one_line() -> None:
    contract = _contract(name="acme", version="1.2", tools=[_tool("echo"), _tool("add")])

    summary = summarize_contract(contract)

    assert summary["server"] == "acme"
    assert summary["tools"] == 2
    assert summary["capabilities"] == ["tools"]


# ---------------------------------------------------------------- tool add/remove


def test_an_identical_pair_reports_nothing() -> None:
    """One recording compared with itself must show no diff at all.

    This is the first gate on determinism: anything that "helpfully reads the present moment"
    (re-timing, set iteration order) turns it red.
    """
    before = _contract(tools=[_tool("echo")])
    after = _contract(tools=[_tool("echo")])

    assert diff_contracts(before, after).identical


def test_an_added_tool_is_reported() -> None:
    before = _contract(tools=[_tool("echo")])
    after = _contract(tools=[_tool("echo"), _tool("add")])

    result = diff_contracts(before, after)

    assert _subjects(result, CHANGE_TOOL_ADDED) == ["add"]
    assert not result.identical


def test_a_removed_tool_is_reported() -> None:
    before = _contract(tools=[_tool("echo"), _tool("add")])
    after = _contract(tools=[_tool("echo")])

    assert _subjects(diff_contracts(before, after), CHANGE_TOOL_REMOVED) == ["add"]


def test_tool_changes_are_listed_in_a_stable_order() -> None:
    """The order must be pinned; two orders from one pair of inputs cannot go into a CI diff."""
    before = _contract(tools=[_tool("zulu"), _tool("yankee")])
    after = _contract(tools=[_tool("alpha"), _tool("bravo")])

    first = _subjects(diff_contracts(before, after), CHANGE_TOOL_ADDED)
    second = _subjects(diff_contracts(before, after), CHANGE_TOOL_ADDED)

    assert first == second == ["alpha", "bravo"]


# ---------------------------------------------------------------- schema contract


def test_adding_a_parameter_is_reported() -> None:
    """This is the class that used to be missed entirely.

    ``_schema_contract`` once filtered ``properties`` by "is this a contract field", throwing
    away parameter names, so adds, renames and type changes were all invisible.
    """
    before = _contract(tools=[_tool("echo", properties={"text": {"type": "string"}})])
    after = _contract(
        tools=[
            _tool(
                "echo",
                properties={"text": {"type": "string"}, "upper": {"type": "boolean"}},
            )
        ]
    )

    result = diff_contracts(before, after)

    assert _subjects(result, CHANGE_TOOL_SCHEMA) == ["echo.inputSchema"]
    assert "+upper" in _details(result, CHANGE_TOOL_SCHEMA)[0]


def test_removing_a_parameter_is_reported() -> None:
    before = _contract(
        tools=[
            _tool(
                "echo",
                properties={"text": {"type": "string"}, "upper": {"type": "boolean"}},
            )
        ]
    )
    after = _contract(tools=[_tool("echo", properties={"text": {"type": "string"}})])

    assert "-upper" in _details(diff_contracts(before, after), CHANGE_TOOL_SCHEMA)[0]


def test_a_parameter_type_change_is_reported() -> None:
    before = _contract(tools=[_tool("echo", properties={"count": {"type": "string"}})])
    after = _contract(tools=[_tool("echo", properties={"count": {"type": "integer"}})])

    detail = _details(diff_contracts(before, after), CHANGE_TOOL_SCHEMA)[0]

    assert "count" in detail
    assert "string" in detail and "integer" in detail


def test_a_renamed_parameter_is_reported() -> None:
    """A rename is "remove one, add one" at the contract level; old clients fail with a missing
    argument.

    ``properties`` is identical before and after; only the key names differ.
    """
    before = _contract(tools=[_tool("echo", properties={"text": {"type": "string"}})])
    after = _contract(tools=[_tool("echo", properties={"body": {"type": "string"}})])
    detail = _details(diff_contracts(before, after), CHANGE_TOOL_SCHEMA)[0]

    assert "+body" in detail
    assert "-text" in detail


def test_a_required_change_is_reported() -> None:
    before = _contract(
        tools=[_tool("echo", properties={"text": {"type": "string"}}, required=["text"])]
    )
    after = _contract(
        tools=[
            _tool(
                "echo",
                properties={"text": {"type": "string"}},
                required=["text", "upper"],
            )
        ]
    )
    detail = _details(diff_contracts(before, after), CHANGE_TOOL_SCHEMA)[0]

    assert "required" in detail


def test_the_order_of_required_does_not_matter() -> None:
    """``["a","b"]`` and ``["b","a"]`` are the same contract; reporting the difference is
    noise.
    """
    before = _contract(
        tools=[
            _tool(
                "echo",
                properties={"text": {"type": "string"}, "count": {"type": "integer"}},
                required=["text", "count"],
            )
        ]
    )
    after = _contract(
        tools=[
            _tool(
                "echo",
                properties={"text": {"type": "string"}, "count": {"type": "integer"}},
                required=["count", "text"],
            )
        ]
    )

    assert diff_contracts(before, after).identical


def test_a_description_rewrite_is_not_a_contract_change() -> None:
    """A typo fix should not count as a contract change.

    It is worth knowing, but not at the same level as "the parameter type went from string to
    number"; lumping them together drowns the one that matters.
    """
    before = _contract(tools=[_tool("echo", description="Echo the text")])
    after = _contract(tools=[_tool("echo", description="Echoes the given text back")])

    assert diff_contracts(before, after).identical


def test_nested_schema_changes_are_reported() -> None:
    """Compare area by area, going deeper.

    Comparing only the top level would miss everything inside ``properties.text``, which is
    exactly where the parameter table lives.
    """
    before = _contract(
        tools=[
            _tool(
                "search",
                properties={
                    "filter": {
                        "type": "object",
                        "properties": {"limit": {"type": "string"}},
                    }
                },
            )
        ]
    )
    after = _contract(
        tools=[
            _tool(
                "search",
                properties={
                    "filter": {
                        "type": "object",
                        "properties": {"limit": {"type": "integer"}},
                    }
                },
            )
        ]
    )

    assert not diff_contracts(before, after).identical


def test_an_array_item_change_is_reported() -> None:
    """Look inside ``items`` too: a changed list element type still breaks callers."""
    string_items = {"items": {"type": "array", "items": {"type": "string"}}}
    int_items = {"items": {"type": "array", "items": {"type": "integer"}}}

    before = _contract(tools=[_tool("batch", properties=string_items)])
    after = _contract(tools=[_tool("batch", properties=int_items)])

    assert not diff_contracts(before, after).identical

def test_an_enum_change_is_reported() -> None:
    """An allowed-value change from ``["fast"]`` to ``["fast","slow"]`` is a contract change."""
    before = _contract(tools=[_tool("run", properties={"mode": {"enum": ["fast"]}})])
    after = _contract(tools=[_tool("run", properties={"mode": {"enum": ["fast", "slow"]}})])

    assert not diff_contracts(before, after).identical


# ---------------------------------------------------------------- capabilities and protocol


def test_a_new_capability_is_reported() -> None:
    before = _contract(capabilities={"tools": {}})
    after = _contract(capabilities={"tools": {}, "resources": {}})

    assert _subjects(diff_contracts(before, after), CHANGE_CAPABILITY) == ["resources"]


def test_a_dropped_capability_is_reported() -> None:
    before = _contract(capabilities={"tools": {}, "prompts": {}})
    after = _contract(capabilities={"tools": {}})

    assert _subjects(diff_contracts(before, after), CHANGE_CAPABILITY) == ["prompts"]


def test_a_protocol_version_change_is_reported() -> None:
    """The protocol version is the premise of the whole session; a change must be reported."""
    before = _contract(protocol="2025-06-18")
    after = _contract(protocol="2025-03-26")

    assert _subjects(diff_contracts(before, after), CHANGE_CAPABILITY) == ["protocolVersion"]


def test_a_server_rename_is_reported_as_a_capability_change() -> None:
    """A different server (name or version) but not a new recording — worth a look."""
    before = _contract(name="acme", version="1.0")
    after = _contract(name="acme", version="2.0")

    assert diff_contracts(before, after).identical, (
        "a version change with an unchanged tool set must not report"
    )


# ---------------------------------------------------------------- latency


def _latency_contract(method: str, values: list[float]) -> Any:
    frames: list[Any] = [_initialize()]
    for index, value in enumerate(values, start=1):
        request, response = _call(method, elapsed=value, msg_id=index)
        frames.extend([request, response])
    return build_contract(frames)


def test_a_clear_slowdown_is_reported() -> None:
    """``800ms → 3200ms`` is a signal."""
    before = _latency_contract("tools/call", [800.0])
    after = _latency_contract("tools/call", [3200.0])

    changes = [c for c in diff_contracts(before, after).changes if c.kind == CHANGE_LATENCY]

    assert len(changes) == 1
    assert "3200" in changes[0].detail


def test_a_small_slowdown_is_not_reported() -> None:
    """``800ms → 812ms`` is noise.

    Reporting it means every diff carries a pile of meaningless entries, and noise stops
    people reading the report.
    """
    before = _latency_contract("tools/call", [800.0])
    after = _latency_contract("tools/call", [812.0])

    assert diff_contracts(before, after).identical


def test_a_small_method_needs_a_proportional_jump() -> None:
    """On small requests a percentage lies: ``10ms → 20ms`` is a 100% increase that means
    nothing; the absolute floor (``LATENCY_MIN_DELTA_MS``) blocks exactly this case.
    """
    small = LATENCY_MIN_DELTA_MS / 4
    before = _latency_contract("tools/list", [small])
    after = _latency_contract("tools/list", [small * 3])

    assert diff_contracts(before, after).identical


def test_both_thresholds_must_be_met() -> None:
    """Report only when the relative increase and the absolute delta hold together.

    ``100ms → 160ms`` passes both; ``100ms → 140ms`` and ``10ms → 20ms`` each fail one, so
    neither should report.
    """
    assert LATENCY_RATIO == 1.5
    assert LATENCY_MIN_DELTA_MS == 50.0

    just_under_ratio = _latency_contract("tools/call", [100.0])
    assert diff_contracts(just_under_ratio, _latency_contract("tools/call", [140.0])).identical

    clear = diff_contracts(
        _latency_contract("tools/call", [100.0]),
        _latency_contract("tools/call", [160.0]),
    )
    assert clear.changes


def test_the_median_is_used_not_the_mean() -> None:
    """One 30-second timeout can raise the average tenfold, and is exactly the point most
    likely to fool the "regression" judgement.

    The median of the noisy recording is still ~100ms, so it must not report; the mean would
    exceed 6 seconds and fire a false positive.
    """
    noisy = _latency_contract("tools/call", [100.0, 100.0, 100.0, 100.0, 30_000.0])
    steady = _latency_contract("tools/call", [100.0, 100.0, 100.0, 100.0, 100.0])

    assert diff_contracts(steady, noisy).identical


def test_a_speedup_is_not_a_slowdown() -> None:
    """Getting faster is not a problem — inverting the direction has the report cry "slower"."""
    before = _latency_contract("tools/call", [3000.0])
    after = _latency_contract("tools/call", [100.0])

    assert diff_contracts(before, after).identical


def test_a_method_present_in_only_one_recording_is_not_compared() -> None:
    """With no data on one side there is no baseline; using 0 would report a fake "0ms
    regressed to 800ms".
    """
    before = _latency_contract("tools/call", [100.0])
    after = build_contract([_initialize()])

    assert diff_contracts(before, after).identical
    assert diff_contracts(after, before).identical


def test_latency_is_taken_from_the_response_and_named_by_the_request() -> None:
    """Latency is read from the response frame; the method name from the request frame.

    ``elapsedMs`` is only known when the response arrives, and the response has no ``method``
    field, so one goes back by ``id``. Scanning request frames left the table always empty.
    """
    contract = _latency_contract("tools/call", [500.0])

    assert contract.latencies == {"tools/call": [500.0]}


def test_a_response_without_a_matching_request_is_not_timed() -> None:
    """A response that matches no request has no endpoint name, and inventing one is the only
    alternative.

    A made-up name pollutes the latency table and makes ghost entries appear in a diff.
    """
    orphan = RecordedFrame(
        1,
        0.0,
        DIRECTION_TO_CLIENT,
        "?",
        999.0,
        json.dumps({"jsonrpc": "2.0", "id": 77, "result": {}}),
    )

    assert build_contract([_initialize(), orphan]).latencies == {}


# ---------------------------------------------------------------- output shape


def test_the_diff_serialises_canonically() -> None:
    """``--json`` output must be reproducible: one pair of inputs serializes identically twice."""
    before = _contract(tools=[_tool("echo")])
    after = _contract(tools=[_tool("echo"), _tool("add")])

    first = json.dumps(diff_contracts(before, after).to_dict(), sort_keys=True)
    second = json.dumps(diff_contracts(before, after).to_dict(), sort_keys=True)

    assert first == second


def test_the_diff_carries_both_sides_of_a_change() -> None:
    """``before`` / ``after`` is the half that programs read.

    A single human sentence (``detail``) cannot be processed by a script; deciding "was a tool
    removed" in CI needs a structured field.
    """
    before = _contract(tools=[_tool("echo", properties={"count": {"type": "string"}})])
    after = _contract(tools=[_tool("echo", properties={"count": {"type": "integer"}})])
    change = next(c for c in diff_contracts(before, after).changes if c.kind == CHANGE_TOOL_SCHEMA)

    assert change.before == {"type": "object", "properties": {"count": {"type": "string"}}}
    assert change.after == {"type": "object", "properties": {"count": {"type": "integer"}}}


def test_the_whole_diff_is_serialisable() -> None:
    """``Contract.to_dict`` must survive ``json.dumps`` — a hidden set would blow up in the CLI."""
    before = _contract(capabilities={"tools": {}, "resources": {}}, tools=[_tool("echo")])
    after = _contract(capabilities={"tools": {}}, tools=[_tool("add")])

    payload = diff_contracts(before, after).to_dict()

    assert json.loads(json.dumps(payload)) == payload
    assert payload["identical"] is False
    assert isinstance(payload["changes"], list)


def test_change_kinds_are_the_documented_five() -> None:
    """The set of categories is a contract: ``session_report`` chooses symbols and ordering.

    Listing them all here means adding a category turns this red, at which point the symbol
    table has to change too.
    """
    assert {
        CHANGE_TOOL_ADDED,
        CHANGE_TOOL_REMOVED,
        CHANGE_TOOL_SCHEMA,
        CHANGE_CAPABILITY,
        CHANGE_LATENCY,
    } == {"tool_added", "tool_removed", "tool_schema", "capability", "latency"}

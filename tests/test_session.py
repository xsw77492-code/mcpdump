"""Session-layer integration tests: really start a child-process MCP server and talk to it."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from mcpdump.core import MCPSession, ServerGoneError, ServerInfo, build_transport, split_command
from mcpdump.core.jsonrpc import JsonRpcError


def _session(server_cmd: str, **kwargs: Any) -> MCPSession:
    return MCPSession(build_transport(server_cmd), **kwargs)


def test_split_command_windows_paths_survive() -> None:
    """Regression test: shlex in posix mode eats the backslashes of a Windows path as escapes."""
    argv = split_command(r'"C:\Program Files\Python\python.exe" "D:\a b\server.py"')
    assert argv[0].endswith("python.exe")
    assert argv[0].startswith("C:")
    assert "\\" in argv[0]
    assert argv[1] == r"D:\a b\server.py"


def test_describe_capabilities_uses_canonical_order_and_keeps_unknown() -> None:
    """Known capabilities come in a fixed order, so output stays diffable; unknown ones are
    appended verbatim.
    """
    server = ServerInfo(capabilities={"prompts": {}, "tools": {}, "future-thing": {}})
    assert server.describe_capabilities() == ["tools", "prompts", "future-thing"]


def test_initialize_reports_server_identity(echo_server_cmd: str) -> None:
    with _session(echo_server_cmd) as session:
        assert session.server.name == "echo-server"
        assert session.server.version == "0.1.0"
        assert session.server.protocol_version
        assert session.server.instructions


def test_list_tools_returns_declared_tools(echo_server_cmd: str) -> None:
    with _session(echo_server_cmd) as session:
        names = [t["name"] for t in session.list_tools()]
    assert names == ["echo", "add", "boom"]


def test_call_tool_roundtrip(echo_server_cmd: str) -> None:
    with _session(echo_server_cmd) as session:
        result = session.call_tool("echo", {"text": "你好"})
    assert result["isError"] is False
    assert result["content"][0]["text"] == "你好"


def test_call_tool_surfaces_is_error_flag(echo_server_cmd: str) -> None:
    with _session(echo_server_cmd) as session:
        result = session.call_tool("boom", {})
    assert result["isError"] is True


def test_call_unknown_tool_raises_jsonrpc_error(echo_server_cmd: str) -> None:
    with _session(echo_server_cmd) as session:
        with pytest.raises(JsonRpcError) as info:
            session.call_tool("nope", {})
    assert info.value.code == -32602


def test_capability_gating_skips_undeclared_capabilities(tools_only_server_cmd: str) -> None:
    """When a server declares only tools, resources/list and prompts/list must not be called."""
    with _session(tools_only_server_cmd) as session:
        assert session.server.supports("tools")
        assert not session.server.supports("resources")
        assert session.list_resources() == []
        assert session.list_prompts() == []
        assert [t["name"] for t in session.list_tools()] == ["ping"]


def test_exchanges_record_wire_level_traffic(echo_server_cmd: str) -> None:
    """The session records only frames it actually sent, and makes no extra requests for callers."""
    with _session(echo_server_cmd) as session:
        session.call_tool("echo", {"text": "x"})
        methods = [ex.method for ex in session.exchanges]
        response_lines = [ex.response_line for ex in session.exchanges]
    assert methods == ["initialize", "tools/call"]
    assert all(line is not None for line in response_lines)
    assert all(ex.elapsed_ms >= 0 for ex in session.exchanges)


def test_missing_executable_gives_actionable_error() -> None:
    session = _session("definitely-not-a-real-binary-xyz")
    with pytest.raises(RuntimeError, match="Cannot find executable"):
        with session:
            pass


def test_a_dead_server_is_reported_as_gone_not_as_a_timeout() -> None:
    """A dead server must report "gone" immediately, not a timeout: a timeout means "it is
    slow", gone means "it is gone", opposite investigations. The assertion compares the exit
    code, since otherwise the most useful clue is lost.
    """
    dying = f'"{sys.executable}" -c "import sys; sys.exit(7)"'

    with pytest.raises(ServerGoneError, match="exited with code 7"):
        with _session(dying, timeout=10.0):
            pass

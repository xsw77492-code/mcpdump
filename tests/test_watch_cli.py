"""End-to-end tests for ``mcpdump watch``.

watch runs as a real server: stdout must carry protocol frames only, all human output goes
to stderr, and the exit code is the real server's, not the proxy's.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any

from conftest import DEMO_SERVER_ARGS, ROOT, run_mcpdump
from mcpdump.services.recorder import (
    DIRECTION_TO_CLIENT,
    DIRECTION_TO_SERVER,
    FORMAT_VERSION,
    load_frames,
    load_header,
)

#: The line the server itself prints. The assertion must compare the ``[server] `` prefix
#: too, else the banner's own text could make the "already suppressed" check pass falsely.
SERVER_NOTE = "[server] server says hi"

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0"},
    },
}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _frames(*messages: dict[str, Any]) -> bytes:
    return b"".join(json.dumps(m).encode() + b"\n" for m in messages)


def _drive(
    args: list[str],
    payload: bytes,
    env: dict[str, str],
    timeout: float = 90.0,
) -> subprocess.CompletedProcess[bytes]:
    """Launch watch as a server, feed it ``payload``, collect its stdout / stderr.

    ``communicate`` fills stdin then closes it; that EOF is how the proxy sends the
    subprocess off and exits, and hand-reading the pipes easily misses it.
    """
    return subprocess.run(
        [sys.executable, "-m", "mcpdump", "watch", *args],
        input=payload,
        capture_output=True,
        env=env,
        timeout=timeout,
        cwd=ROOT,
    )


def _watch_echo() -> str:
    """``mcpdump watch <echo server>`` as a launch command, for an outer mcpdump to use.

    Quoting is two layers deep and the two layers must not use the same quote character, or
    the outermost pair closes early and watch exits with a usage error.
    """
    inner = " ".join(f"'{part}'" for part in (sys.executable, *DEMO_SERVER_ARGS))
    return f'"{sys.executable}" -m mcpdump watch "{inner}"'


# ---------------------------------------------------------------- protocol channel


def test_stdout_carries_frames_and_nothing_else(
    echo_server_cmd: str, mcpdump_env: dict[str, str]
) -> None:
    """stdout may carry the server's frames only; no character of the human banner may leak in."""
    proc = _drive([echo_server_cmd], _frames(INITIALIZE), mcpdump_env)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count(b"\n") == 1
    response = json.loads(proc.stdout)
    assert response["id"] == 1
    assert response["result"]["serverInfo"]["name"] == "echo-server"

    assert b"mcpdump watch" not in proc.stdout
    assert b"stdout carries the protocol" not in proc.stdout


def test_human_view_goes_to_stderr(
    echo_server_cmd: str, mcpdump_env: dict[str, str]
) -> None:
    """Both the banner and the per-frame view land on stderr."""
    proc = _drive([echo_server_cmd], _frames(INITIALIZE), mcpdump_env)

    assert b"mcpdump watch" in proc.stderr
    assert b"stdout carries the protocol" in proc.stderr
    assert b"\xe2\x86\x92 initialize" in proc.stderr  # → initialize
    assert b"\xe2\x86\x90 initialize" in proc.stderr  # ← initialize
    assert b"sent" in proc.stderr and b"received" in proc.stderr


def test_quiet_server_hides_the_servers_own_stderr(
    wire_server: Callable[[str], str], mcpdump_env: dict[str, str]
) -> None:
    """``--quiet-server`` mutes only the server's own output; the proxy's view is unchanged."""
    server = wire_server('--stderr "server says hi"')

    noisy = _drive([server], b"", mcpdump_env)
    quiet = _drive([server, "--quiet-server"], b"", mcpdump_env)

    assert SERVER_NOTE.encode() in noisy.stderr
    assert SERVER_NOTE.encode() not in quiet.stderr
    assert b"mcpdump watch" in quiet.stderr


# ---------------------------------------------------------------- real session


def test_a_real_mcpdump_session_runs_through_the_proxy(mcpdump_env: dict[str, str]) -> None:
    """Let mcpdump act as the client and reach the server through the proxy.

    The strongest proof of transparency: the outer mcpdump runs the full handshake and
    capability discovery, so any altered or dropped frame shows up here.
    """
    proc = run_mcpdump(["ls", _watch_echo(), "--quiet-server", "--json"], mcpdump_env)

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["server"]["name"] == "echo-server"
    assert {t["name"] for t in payload["tools"]} == {"echo", "add", "boom"}
    assert payload["prompts"][0]["name"] == "greet"


def test_a_tool_call_survives_the_proxy(mcpdump_env: dict[str, str]) -> None:
    """Tool calls pass through too: arguments and return values arrive verbatim."""
    proc = run_mcpdump(
        [
            "call",
            _watch_echo(),
            "echo",
            "--args",
            '{"text":"hello proxy"}',
            "--json",
            "--quiet-server",
        ],
        mcpdump_env,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["result"]["content"][0]["text"] == "hello proxy"


# ---------------------------------------------------------------- recording


def test_record_writes_one_json_object_per_frame(
    echo_server_cmd: str, mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """``--record`` writes line-delimited JSON, every field present and sequence contiguous.

    It goes through ``iter_frames`` rather than parsing each line, so the record format keeps
    one definition: re-implementing it here would let writer and test drift together.
    """
    output = tmp_path / "out.jsonl"
    payload = _frames(INITIALIZE, INITIALIZED, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    proc = _drive([echo_server_cmd, "--record", str(output)], payload, mcpdump_env)

    assert proc.returncode == 0, proc.stderr
    header = load_header(output)
    assert header.version == FORMAT_VERSION
    assert header.argv, (
        "the launch command must stay in the header so the recording can be "
        "traced back to a server"
    )

    frames = load_frames(output)
    assert frames, "the recording file is empty"
    assert [frame.seq for frame in frames] == list(range(1, len(frames) + 1))
    assert all(frame.direction in (DIRECTION_TO_SERVER, DIRECTION_TO_CLIENT) for frame in frames)
    assert all(frame.at_ms >= 0 for frame in frames)

    # The line field is the **raw message** and must parse back to JSON-RPC: replay depends on it.
    for frame in frames:
        assert json.loads(frame.line)["jsonrpc"] == "2.0"


def test_record_keeps_request_before_response(
    echo_server_cmd: str, mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """Causal order in the recording: a response cannot precede the request that triggered it."""
    output = tmp_path / "out.jsonl"
    payload = _frames(INITIALIZE, INITIALIZED, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    proc = _drive([echo_server_cmd, "--record", str(output)], payload, mcpdump_env)

    assert proc.returncode == 0, proc.stderr
    frames = load_frames(output)
    issued = {frame.method: frame.seq for frame in frames if frame.to_server}
    for frame in frames:
        if frame.to_server:
            continue
        assert frame.method in issued
        assert issued[frame.method] < frame.seq
        assert frame.elapsed_ms is not None


def test_unwritable_record_path_fails_before_starting_anything(
    echo_server_cmd: str, mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """If the recording file cannot be written, fail at once rather than run first."""
    missing = tmp_path / "no-such-dir" / "frames.jsonl"

    proc = _drive([echo_server_cmd, "--record", str(missing)], _frames(INITIALIZE), mcpdump_env)

    assert proc.returncode == 1
    assert b"Cannot write the recording" in proc.stderr


# ---------------------------------------------------------------- config and exit codes


def test_print_config_emits_pure_json(echo_server_cmd: str, mcpdump_env: dict[str, str]) -> None:
    """``--print-config`` output must be redirectable straight into mcp.json."""
    proc = run_mcpdump(["watch", "--print-config", echo_server_cmd], mcpdump_env)

    assert proc.returncode == 0, proc.stderr
    config = json.loads(proc.stdout)  # any decorative text at all blows up here
    entry = next(iter(config["mcpServers"].values()))
    assert entry["command"] == "mcpdump"
    assert entry["args"][0] == "watch"
    assert entry["args"][1:] == [sys.executable, *DEMO_SERVER_ARGS]


def test_print_config_names_the_server_from_its_command(
    echo_server_cmd: str, mcpdump_env: dict[str, str]
) -> None:
    """Config keys come from the launch command; unguessable ones fall back to the binary name."""
    proc = run_mcpdump(["watch", "--print-config", "npx -y @acme/weather-mcp /data"], mcpdump_env)

    assert proc.returncode == 0, proc.stderr
    assert list(json.loads(proc.stdout)["mcpServers"]) == ["weather-mcp"]


def test_exit_code_follows_the_server(mcpdump_env: dict[str, str]) -> None:
    """The proxy exit code = the real server's; overwriting it with its own status is a lie."""
    dying = f'"{sys.executable}" -c "import sys; sys.exit(7)"'

    proc = _drive([dying], b"", mcpdump_env)

    assert proc.returncode == 7
    assert b"exited with code 7" in proc.stderr


def test_help_lists_the_watch_options(mcpdump_env: dict[str, str]) -> None:
    proc = run_mcpdump(["watch", "--help"], mcpdump_env)

    assert proc.returncode == 0
    for flag in ("--record", "--print-config", "--full", "--quiet-server"):
        assert flag in proc.stdout

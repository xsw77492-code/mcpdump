"""End-to-end tests for ``mcpdump record`` / ``replay`` / ``diff``.

Nothing is simulated in-process: ``record`` is a transparent server (stdout frames only),
``replay`` is a function of the file alone, and ``diff`` catches real contract changes.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

from conftest import DEMO_SERVER_ARGS, ROOT, run_mcpdump

# ---------------------------------------------------------------- wiring


def _record_command(out: pathlib.Path, *server: str) -> str:
    """``mcpdump record --out <file> <real server>`` as a single launch command.

    Quoting is two layers deep and the two layers must not use the same quote character, and
    the server is passed as argv tokens each wrapped separately: concatenating then quoting
    would fuse ``-m`` with the module name and trigger a usage error.
    """
    inner = " ".join(f"'{part}'" for part in (sys.executable, *server))
    return f'"{sys.executable}" -m mcpdump record --out "{out}" "{inner}"'


def _capture(tmp_path: pathlib.Path, env: dict[str, str]) -> pathlib.Path:
    """Run a real session and drop the recording at ``tmp_path / "session.jsonl"``.

    The client is ``mcpdump ls``, so the recording is contentful (full handshake and all
    ``*/list`` calls) rather than a bare ``initialize``.
    """
    target = tmp_path / "session.jsonl"
    command = _record_command(target, *DEMO_SERVER_ARGS)

    proc = run_mcpdump(["ls", command, "--quiet-server", "--json"], env)

    assert proc.returncode == 0, proc.stderr
    assert target.exists(), "the recording file was not written"
    return target


def _lines(proc: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in proc.stdout.splitlines() if line.strip()]


# ---------------------------------------------------------------- record


def test_record_passes_the_protocol_through_untouched(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """``record`` must be transparent: the client receives exactly what a direct connection
    would give.

    Letting mcpdump itself be the client is the strongest proof: any altered, lost or added
    frame makes the ``--json`` output wrong.
    """
    target = tmp_path / "session.jsonl"
    command = _record_command(target, *DEMO_SERVER_ARGS)

    proc = run_mcpdump(["ls", command, "--quiet-server", "--json"], mcpdump_env)

    assert proc.returncode == 0, proc.stderr
    assert target.exists()

    payload = json.loads(proc.stdout)
    assert payload["server"]["name"] == "echo-server"
    assert {tool["name"] for tool in payload["tools"]} == {"echo", "add", "boom"}


def test_record_keeps_the_human_output_off_stdout(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """stdout is the protocol channel; not one word of banner, progress or summary may leak
    into it.

    ``record`` is run directly here to see whether what it prints and what it sends stay
    apart.
    """
    target = tmp_path / "session.jsonl"
    request = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            }
        )
        + "\n"
    ).encode()

    # The SERVER argument must be an already quoted launch command: ``record`` splits it
    # once more. A bare path would have its backslashes treated as escapes on Windows.
    server = " ".join(f"'{part}'" for part in (sys.executable, *DEMO_SERVER_ARGS))

    proc = subprocess.run(
        [sys.executable, "-m", "mcpdump", "record", "--out", str(target), server],
        input=request,
        capture_output=True,
        env=mcpdump_env,
        timeout=90,
        cwd=ROOT,
    )

    assert proc.returncode == 0, proc.stderr
    # stdout carries exactly one protocol frame, and it parses as a JSON-RPC response.
    frames = [line for line in proc.stdout.decode("utf-8").splitlines() if line.strip()]
    assert len(frames) == 1
    assert json.loads(frames[0])["id"] == 1
    assert b"mcpdump record" not in proc.stdout
    assert b"Wrapping" not in proc.stdout
    assert b"mcpdump record" in proc.stderr


def test_record_writes_a_readable_session(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """What lands on disk must be recognisable to its own reader: header complete,
    sequence contiguous, messages re-parseable."""
    target = _capture(tmp_path, mcpdump_env)

    lines = target.read_text("utf-8").splitlines()
    header = json.loads(lines[0])
    assert header["format"] == "mcpdump-session"
    assert header["version"] == 1

    frames = [json.loads(line) for line in lines[1:]]
    assert [frame["seq"] for frame in frames] == list(range(1, len(frames) + 1))
    assert all(frame["direction"] in ("to_server", "to_client") for frame in frames)
    for frame in frames:
        assert json.loads(frame["line"])["jsonrpc"] == "2.0"


def test_record_stores_the_server_command_for_later(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """``argv`` exists so a human can later tell "which server was this" — a
    recording attached to an issue is useless if all it has is frames."""
    target = _capture(tmp_path, mcpdump_env)

    argv = json.loads(target.read_text("utf-8").splitlines()[0])["argv"]
    assert argv[-1] == "mcpdump.demo"


def test_record_print_config_emits_pure_json(mcpdump_env: dict[str, str]) -> None:
    """``--print-config`` must be redirectable straight into ``mcp.json``.

    The launch command it references must be ``record``, not ``watch``; the wrong one makes
    the user think they are recording when nothing is stored.
    """
    proc = run_mcpdump(
        ["record", "--print-config", "--out", "/tmp/s.jsonl", "npx -y @acme/weather-mcp"],
        mcpdump_env,
    )

    assert proc.returncode == 0, proc.stderr
    config = json.loads(proc.stdout)
    entry = next(iter(config["mcpServers"].values()))
    assert entry["command"] == "mcpdump"
    assert entry["args"][0] == "record"
    assert "--out" in entry["args"]


def test_record_refuses_an_unwritable_path(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """If it cannot write, fail on the spot instead of running first.

    Running a whole session and only then saying "nothing was recorded" wastes the run; this
    is a usage error (1), not an environment error (4).
    """
    missing = tmp_path / "no-such-dir" / "s.jsonl"
    server = " ".join(f"'{part}'" for part in (sys.executable, *DEMO_SERVER_ARGS))

    proc = run_mcpdump(["record", "--out", str(missing), server], mcpdump_env)

    assert proc.returncode == 1
    assert "Cannot record" in proc.stderr or "record" in proc.stderr.lower()


# ---------------------------------------------------------------- replay


def test_replay_reads_a_real_recording(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """Replay a genuinely recorded file: it must report exchange counts, method
    names and server identity."""
    target = _capture(tmp_path, mcpdump_env)

    proc = run_mcpdump(["replay", str(target)], mcpdump_env)

    assert proc.returncode == 0, proc.stderr
    assert "initialize" in proc.stdout
    assert "tools/list" in proc.stdout
    # A notification must be recognised too, not counted as "a request never answered".
    assert "notifications/initialized" in proc.stdout


def test_replay_reports_no_dangling_requests_for_a_healthy_session(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """A healthy session must not report dangling requests.

    ``notifications/initialized`` has no response by spec; counting it as dangling would warn
    on every healthy recording, drowning out the real problems.
    """
    target = _capture(tmp_path, mcpdump_env)

    proc = run_mcpdump(["replay", str(target), "--limit", "0"], mcpdump_env)

    assert proc.returncode == 0, proc.stderr
    assert "Dangling" not in proc.stdout
    assert "Orphan" not in proc.stdout


def test_replay_is_deterministic(mcpdump_env: dict[str, str], tmp_path: pathlib.Path) -> None:
    """Replaying the same recording twice gives byte-identical output.

    The user attaches a recording to an issue, so what someone else gets must match; any
    "just read the present" (recomputing durations, reading terminal width) turns this red.
    """
    target = _capture(tmp_path, mcpdump_env)

    first = run_mcpdump(["replay", str(target), "--limit", "0"], mcpdump_env)
    second = run_mcpdump(["replay", str(target), "--limit", "0"], mcpdump_env)

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout


def test_replay_json_is_parseable_and_stable(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """``--json`` is for scripts: it must be pure JSON, and consistent across runs."""
    target = _capture(tmp_path, mcpdump_env)

    proc = run_mcpdump(["replay", str(target), "--json"], mcpdump_env)

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)  # any decorative text blows up right here
    assert payload["source"] == str(target)
    assert payload["stats"]["exchanges"] >= 3
    assert [step["method"] for step in payload["steps"]][:1] == ["initialize"]


def test_replay_step_mode_shows_the_wire_text(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """``--step`` must include the raw message — otherwise "stepping" shows the same
    thing as plain replay."""
    target = _capture(tmp_path, mcpdump_env)

    plain = run_mcpdump(["replay", str(target)], mcpdump_env)
    stepped = run_mcpdump(["replay", str(target), "--step"], mcpdump_env)

    assert stepped.returncode == 0, stepped.stderr
    assert "jsonrpc" in stepped.stdout
    assert "jsonrpc" not in plain.stdout


def test_replay_limit_says_how_much_it_hid(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """Truncation must never be silent; showing fewer steps without telling the user is worse
    than an error, since replay is used as evidence.
    """
    target = _capture(tmp_path, mcpdump_env)

    proc = run_mcpdump(["replay", str(target), "--limit", "2"], mcpdump_env)

    assert proc.returncode == 0, proc.stderr
    assert "omitted" in proc.stdout.lower() or "not shown" in proc.stdout.lower()


def test_replay_wire_shows_the_frames(mcpdump_env: dict[str, str], tmp_path: pathlib.Path) -> None:
    """``--wire`` reuses the existing ``ui.wire`` layout, keeping the same visual
    language as ``ls``."""
    target = _capture(tmp_path, mcpdump_env)

    proc = run_mcpdump(["replay", str(target), "--wire"], mcpdump_env)

    assert proc.returncode == 0, proc.stderr
    assert "initialize" in proc.stdout


def test_replay_refuses_a_foreign_file(mcpdump_env: dict[str, str], tmp_path: pathlib.Path) -> None:
    """Replaying some other file must be explained **on the spot**, not reported as a
    pile of missing fields."""
    other = tmp_path / "other.jsonl"
    other.write_text('{"timestamp": 1, "level": "info"}\n', encoding="utf-8")

    proc = run_mcpdump(["replay", str(other)], mcpdump_env)

    assert proc.returncode == 1
    assert "mcpdump-session" in proc.stderr


def test_replay_refuses_a_newer_format(mcpdump_env: dict[str, str], tmp_path: pathlib.Path) -> None:
    """A version newer than this program must be refused, with advice on what to do."""
    future = tmp_path / "future.jsonl"
    future.write_text(
        '{"format": "mcpdump-session", "version": 999, "argv": []}\n', encoding="utf-8"
    )

    proc = run_mcpdump(["replay", str(future)], mcpdump_env)

    assert proc.returncode == 1
    assert "999" in proc.stderr


# ---------------------------------------------------------------- diff


def _edit_schema(source: pathlib.Path, target: pathlib.Path, mutate: Any) -> None:
    """Rewrite the ``tools/list`` response in a recording and save it as a new one.

    ``mutate`` returns the changed list and the return value must be captured: drop it and the
    change is silently lost, showing up only as "it was changed but no difference is reported".
    """
    out: list[str] = []
    for line in source.read_text("utf-8").splitlines():
        record = json.loads(line)
        if "line" in record and record.get("direction") == "to_client":
            payload = json.loads(record["line"])
            result = payload.get("result")
            if isinstance(result, dict) and isinstance(result.get("tools"), list):
                result["tools"] = mutate(result["tools"])
                record["line"] = json.dumps(payload, ensure_ascii=False)
        out.append(json.dumps(record, ensure_ascii=False))
    target.write_text("\n".join(out) + "\n", encoding="utf-8")


def test_diff_of_two_recordings_of_the_same_session_is_empty(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """Recording the same session twice must make diff report no difference and exit 0.

    Recordings carry per-run ``seq`` / ``atMs`` / ``elapsedMs``, so a textual diff would say
    the whole file changed; a structural one must not be swayed by them.
    """
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = _capture(tmp_path / "a", mcpdump_env)
    second = _capture(tmp_path / "b", mcpdump_env)

    proc = run_mcpdump(["diff", str(first), str(second)], mcpdump_env)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No structural differences" in proc.stdout


def test_diff_of_a_recording_against_itself_is_empty(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """Compared with itself there is never a difference; stricter, since not even durations
    may differ.
    """
    target = _capture(tmp_path, mcpdump_env)

    proc = run_mcpdump(["diff", str(target), str(target)], mcpdump_env)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "No structural differences" in proc.stdout


def test_diff_reports_a_removed_tool(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """A removed tool is the most serious change: old clients simply cannot call it.

    Exit code ``2`` reuses ``check``'s "violations exist" — for CI, "behaviour changed" and
    "does not meet the spec" both need a human eye.
    """
    original = _capture(tmp_path, mcpdump_env)
    edited = tmp_path / "edited.jsonl"
    _edit_schema(original, edited, lambda tools: [t for t in tools if t["name"] != "echo"])

    proc = run_mcpdump(["diff", str(original), str(edited)], mcpdump_env)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "echo" in proc.stdout
    assert "removed" in proc.stdout.lower()


def test_diff_reports_a_new_tool_schema_parameter(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """Parameter add/remove is the easiest class to miss; here it runs against a real recording.

    The schema comes from the demo server (``mcpdump.demo``), not hand-built in the test.
    """
    original = _capture(tmp_path, mcpdump_env)
    edited = tmp_path / "edited.jsonl"

    def add_parameter(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for tool in tools:
            if tool["name"] == "echo":
                tool["inputSchema"]["properties"]["upper"] = {"type": "boolean"}
        return tools

    _edit_schema(original, edited, add_parameter)

    proc = run_mcpdump(["diff", str(original), str(edited)], mcpdump_env)

    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "echo.inputSchema" in proc.stdout
    assert "upper" in proc.stdout


def test_diff_json_is_parseable(
    mcpdump_env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    """``--json`` is for CI: it must parse, and carry structured ``before`` / ``after``
    data."""
    original = _capture(tmp_path, mcpdump_env)
    edited = tmp_path / "edited.jsonl"
    _edit_schema(original, edited, lambda tools: [t for t in tools if t["name"] != "add"])

    proc = run_mcpdump(["diff", str(original), str(edited), "--json"], mcpdump_env)

    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["identical"] is False
    change = next(c for c in payload["changes"] if c["kind"] == "tool_removed")
    assert change["subject"] == "add"


def test_diff_refuses_a_foreign_file(mcpdump_env: dict[str, str], tmp_path: pathlib.Path) -> None:
    """When one of the two inputs is not a recording, say which one is at fault, on
    the spot."""
    target = _capture(tmp_path, mcpdump_env)
    other = tmp_path / "other.jsonl"
    other.write_text('{"timestamp": 1}\n', encoding="utf-8")

    proc = run_mcpdump(["diff", str(target), str(other)], mcpdump_env)

    assert proc.returncode == 1
    assert "mcpdump-session" in proc.stderr


def test_diff_needs_two_paths(mcpdump_env: dict[str, str]) -> None:
    """One recording alone is a usage error: "compare with itself" and "compare with another"
    are ambiguous, and the program should not guess.
    """
    proc = run_mcpdump(["diff", "only-one.jsonl"], mcpdump_env)

    assert proc.returncode != 0


# ---------------------------------------------------------------- help


@pytest.mark.parametrize("command", ["record", "replay", "diff"])
def test_every_new_command_has_help(command: str, mcpdump_env: dict[str, str]) -> None:
    proc = run_mcpdump([command, "--help"], mcpdump_env)

    assert proc.returncode == 0
    assert command in proc.stdout


def test_the_main_help_lists_the_new_commands(mcpdump_env: dict[str, str]) -> None:
    proc = run_mcpdump(["--help"], mcpdump_env)

    assert proc.returncode == 0
    for command in ("record", "replay", "diff"):
        assert command in proc.stdout

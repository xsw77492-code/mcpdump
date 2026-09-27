"""End-to-end tests for ``mcpdump mock``.

Its value is cross-process: leg one records from a real server (``ls`` → ``record`` → echo),
leg two serves a client from the recording alone, proving mock stood in for the server.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

from conftest import DEMO_SERVER_ARGS, ROOT, run_mcpdump
from mcpdump import i18n

# ---------------------------------------------------------------- chain


def _wrapped(*inner: str) -> str:
    """Join argv parts into a ``record`` SERVER argument.

    The result carries only the inner quotes: each part is wrapped in its own single quotes so
    ``split_command`` can split it back. The outermost quotes are the caller's job.
    """
    return " ".join(f"'{part}'" for part in inner)


def _record_session(tmp_path: pathlib.Path, env: dict[str, str]) -> pathlib.Path:
    """Run one real session and produce a recording that includes a handshake.

    The client is ``mcpdump ls``: full handshake, capability discovery and the three ``*/list``
    calls, but no tool call, so this recording has no answer to ``tools/call``.
    """
    target = tmp_path / "session.jsonl"
    inner = _wrapped(sys.executable, *DEMO_SERVER_ARGS)
    # The outer quotes are added here: ``inner`` must be embedded into ``ls``'s SERVER
    # argument and become a single token, otherwise ``ls`` reads it as three commands.
    command = f'"{sys.executable}" -m mcpdump record --out "{target}" "{inner}"'

    proc = run_mcpdump(["ls", command, "--quiet-server", "--json"], env)
    assert proc.returncode == 0, proc.stderr
    assert target.exists(), "the recording file was not written"
    return target


def _record_with_a_tool_call(
    tmp_path: pathlib.Path, env: dict[str, str], *, name: str = "echo"
) -> pathlib.Path:
    """Record a session that contains one real tool call.

    It runs the real ``record`` rather than hand-writing JSONL, so the test covers "mock reads
    what record writes"; and ``inner`` is one argv element because ``record`` splits it itself.
    """
    target = tmp_path / "with-call.jsonl"
    inner = _wrapped(sys.executable, *DEMO_SERVER_ARGS)

    requests = "\n".join([
        json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "probe", "version": "1"},
            },
        }),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": name, "arguments": {"text": "hi"}},
        }),
    ])

    proc = subprocess.run(
        [sys.executable, "-m", "mcpdump", "record", "--out", str(target), inner],
        input=requests + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60.0,
        cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert target.exists()

    # Confirm the call really was recorded — otherwise the tests below run on an empty premise.
    recorded = target.read_text(encoding="utf-8")
    assert "tools/call" in recorded, "tools/call was not recorded"
    return target


def _mock_command(record: pathlib.Path) -> str:
    """``mcpdump mock <recording>`` as a command a client can launch."""
    return f"{sys.executable} -m mcpdump mock {record}"


# ---------------------------------------------------------------- server behaviour


class TestServesARecording:
    """Serve a recording to a real client as if it were the server."""

    def test_a_real_client_can_connect_with_no_server_running(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """This is why the command exists: no process other than the recording is running."""
        record = _record_session(tmp_path, mcpdump_env)

        proc = run_mcpdump(["ls", _mock_command(record), "--quiet-server", "--json"], mcpdump_env)

        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["server"]["name"]
        assert payload["tools"], "mock did not hand back the tools recorded earlier"

    def test_the_tools_are_the_ones_from_the_recording(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        record = _record_session(tmp_path, mcpdump_env)

        proc = run_mcpdump(["ls", _mock_command(record), "--quiet-server", "--json"], mcpdump_env)

        names = [tool["name"] for tool in json.loads(proc.stdout)["tools"]]
        assert names, "the recorded tool list is empty"

    def test_a_tool_call_works_through_the_mock(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """Not only lists tools, but really calls one: the client gets the recorded result.

        ``mcpdump ls`` never calls a tool, so a recording with a tool call has to be made
        separately; otherwise this only hits the "mock says it has no answer" branch.
        """
        record = _record_with_a_tool_call(tmp_path, mcpdump_env)
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 42,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"text": "hi"}},
            }
        )
        proc = subprocess.run(
            [sys.executable, "-m", "mcpdump", "mock", str(record)],
            input=request + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=30.0,
            cwd=ROOT,
        )

        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        assert len(lines) == 1
        payload: Any = json.loads(lines[0])
        assert payload["id"] == 42
        # The result comes straight from the recording; it may be a success or an error.
        # But it must **never be -32601** — that would mean mock found no answer.
        assert "result" in payload or "error" in payload
        if "error" in payload:
            assert payload["error"]["code"] != -32601

    def test_the_recorded_result_reaches_the_client(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """What the client gets must be the result recorded at the time, not any old success."""
        record = _record_with_a_tool_call(tmp_path, mcpdump_env)
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"text": "hi"}},
            }
        )
        proc = subprocess.run(
            [sys.executable, "-m", "mcpdump", "mock", str(record)],
            input=request + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=30.0,
            cwd=ROOT,
        )

        payload = json.loads(proc.stdout.strip())
        # The echo tool echoes its arguments — "hi" is recorded, so it must come back.
        assert "hi" in json.dumps(payload)


class TestRefusesBadInput:
    """An unusable recording must be refused now, not served by an unreachable fake server."""

    def test_a_recording_without_a_handshake_is_refused(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """Without an answer to ``initialize`` it cannot start — the client would stall in the
        handshake and time out.

        That reports "server not responding", unrelated to the real cause, so refusing now is
        better.
        """
        target = tmp_path / "half.jsonl"
        header = {
            "format": "mcpdump-session",
            "version": 1,
            "at": "2026-01-01T00:00:00+00:00",
            "argv": ["echo"],
        }
        frame = {
            "seq": 1,
            "atMs": 1.0,
            "direction": "to_server",
            "method": "tools/list",
            "elapsedMs": None,
            "line": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        }
        target.write_text(
            json.dumps(header) + "\n" + json.dumps(frame) + "\n", encoding="utf-8"
        )

        proc = run_mcpdump(["mock", str(target)], mcpdump_env, timeout=20.0)

        assert proc.returncode != 0
        # The error must say what is missing, and how to supply it.
        assert "initialize" in proc.stderr

    def test_a_missing_file_is_reported_clearly(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        missing = tmp_path / "nope.jsonl"
        proc = run_mcpdump(["mock", str(missing)], mcpdump_env, timeout=20.0)

        assert proc.returncode != 0
        assert "nope.jsonl" in proc.stderr

    def test_a_foreign_file_is_refused(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """A foreign file must be reported as such, not parsed as a broken recording."""
        foreign = tmp_path / "other.jsonl"
        foreign.write_text('{"hello": "world"}\n', encoding="utf-8")

        proc = run_mcpdump(["mock", str(foreign)], mcpdump_env, timeout=20.0)

        assert proc.returncode != 0
        assert "mcpdump-session" in proc.stderr


class TestStdoutPurity:
    """stdout is the protocol channel; human-readable text must not get mixed in."""

    def test_the_banner_goes_to_stderr_not_stdout(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """A banner mixed into stdout instantly breaks the client's JSON-RPC parser."""
        record = _record_session(tmp_path, mcpdump_env)
        proc = run_mcpdump(["mock", str(record)], mcpdump_env, timeout=20.0)

        # No client connects, so stdin hits EOF at once and stdout carries **nothing**.
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        assert lines == []
        assert "Mocking from" in proc.stderr

    def test_every_stdout_line_is_a_json_object(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """Feed messages straight into mock and check every line it returns is valid JSON-RPC."""
        record = _record_session(tmp_path, mcpdump_env)
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"},
                },
            }
        )

        proc = subprocess.run(
            [sys.executable, "-m", "mcpdump", "mock", str(record)],
            input=request + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=30.0,
            cwd=ROOT,
        )
        assert proc.returncode == 0, proc.stderr
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["id"] == 1


class TestNotificationHandling:
    """A notification must not get a response."""

    def test_a_notification_produces_no_output(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """Found by running it for real: mock used to answer ``notifications/initialized`` with
        an ``id: null`` error.

        A client receiving a response it did not wait for is undefined behaviour; strict
        implementations disconnect.
        """
        record = _record_session(tmp_path, mcpdump_env)
        initial = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"},
                },
            }
        )
        notification = json.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )

        proc = subprocess.run(
            [sys.executable, "-m", "mcpdump", "mock", str(record)],
            input=initial + "\n" + notification + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=30.0,
            cwd=ROOT,
        )

        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        assert len(lines) == 1, f"a notification was replied to: {lines}"
        assert json.loads(lines[0])["id"] == 1

    def test_the_summary_counts_notifications_separately(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        record = _record_session(tmp_path, mcpdump_env)
        initial = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"},
                },
            }
        )
        notification = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})

        proc = subprocess.run(
            [sys.executable, "-m", "mcpdump", "mock", str(record)],
            input=initial + "\n" + notification + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=30.0,
            cwd=ROOT,
        )

        # Notifications are normal, so they must not raise "unmatched" for every session.
        assert "0 unmatched" in proc.stderr


class TestIdRewrite:
    """The response must be rewritten to the client's id when it differs from the recorded one."""

    def test_the_client_gets_its_own_id_back(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        record = _record_session(tmp_path, mcpdump_env)
        # At recording time the client used its own id (probably starting from 1).
        # Deliberately pick a value that can never collide here.
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 987654,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"},
                },
            }
        )

        proc = subprocess.run(
            [sys.executable, "-m", "mcpdump", "mock", str(record)],
            input=request + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=30.0,
            cwd=ROOT,
        )

        assert proc.returncode == 0, proc.stderr
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        assert lines
        assert json.loads(lines[0])["id"] == 987654


class TestUnknownMethod:
    """A method absent from the recording errors rather than faking success.

    The method name used exists for real but is not in the recording: ``echo_server`` declares
    tools / resources / prompts, so all three ``*/list`` calls were recorded.
    """

    def test_an_unrecorded_method_gets_method_not_found(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        record = _record_session(tmp_path, mcpdump_env)
        request = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "logging/setLevel"})

        proc = subprocess.run(
            [sys.executable, "-m", "mcpdump", "mock", str(record)],
            input=request + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=30.0,
            cwd=ROOT,
        )

        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["id"] == 7
        assert payload["error"]["code"] == -32601

    def test_the_error_names_the_missing_method(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        record = _record_session(tmp_path, mcpdump_env)
        request = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "logging/setLevel"})

        proc = subprocess.run(
            [sys.executable, "-m", "mcpdump", "mock", str(record)],
            input=request + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=30.0,
            cwd=ROOT,
        )

        assert "logging/setLevel" in proc.stdout


class TestMaxRequests:
    """The ``--max-requests`` safety cap."""

    def test_the_limit_stops_the_loop(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """When a client forgets to close the connection, an unbounded mock hangs forever."""
        record = _record_session(tmp_path, mcpdump_env)
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"},
                },
            }
        )

        # Feed five identical requests, but ask it to serve only two.
        proc = subprocess.run(
            [sys.executable, "-m", "mcpdump", "mock", str(record), "--max-requests", "2"],
            input=(request + "\n") * 5,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=30.0,
            cwd=ROOT,
        )

        assert proc.returncode == 0, proc.stderr
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        assert len(lines) == 2


class TestHelpAndRegistration:
    """The command is registered and its help text is complete."""

    def test_mock_appears_in_the_help(self, mcpdump_env: dict[str, str]) -> None:
        proc = run_mcpdump(["--help"], mcpdump_env, timeout=20.0)
        assert "mock" in proc.stdout

    def test_mock_has_its_own_help(self, mcpdump_env: dict[str, str]) -> None:
        proc = run_mcpdump(["mock", "--help"], mcpdump_env, timeout=20.0)
        assert proc.returncode == 0
        assert "FILE" in proc.stdout


@pytest.mark.parametrize("lang", ["en", "zh"])
def test_the_banner_is_translated(lang: str, tmp_path: pathlib.Path) -> None:
    """The banner must render in both languages; a missing placeholder blows up here.

    The whole string is not asserted: Rich wraps to the terminal width, so only the greeting's
    first word is checked.
    """
    from conftest import _make_env

    env = _make_env(lang)
    record = _record_session(tmp_path, env)

    proc = subprocess.run(
        [sys.executable, "-m", "mcpdump", "mock", str(record)],
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=30.0,
        cwd=ROOT,
    )

    # Take the first word of the banner text — it is certainly before any wrapping.
    banner = i18n.t("mock.banner", lang=lang, path=str(record))
    head = banner.split()[0]
    assert head in proc.stderr


class TestFromServer:
    """``--from SERVER``: record once, then serve the client from what was recorded.

    The value is "record once, use many times": keep a server in hand, then iterate on the
    client without starting it.
    """

    def _from_command(self, out: pathlib.Path) -> list[str]:
        """Expand ``mock --from <real server> --out <file>`` into argv.

        The ``--from`` value is one whole launch command including its own quotes, so the whole
        string stays a single argv element; ``mock`` splits it once more after receiving it.
        """
        inner = _wrapped(sys.executable, *DEMO_SERVER_ARGS)
        return [
            sys.executable,
            "-m",
            "mcpdump",
            "mock",
            "--from",
            inner,
            "--out",
            str(out),
            "--quiet-server",
        ]

    def test_it_records_then_serves_in_one_go(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """Record real responses and start the mock in one command."""
        target = tmp_path / "from.jsonl"
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"},
                },
            }
        )

        proc = subprocess.run(
            self._from_command(target),
            input=request + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=60.0,
            cwd=ROOT,
        )

        assert proc.returncode == 0, proc.stderr
        # It recorded, and did not drop the client's request while recording.
        assert target.exists(), "was not written to disk"
        assert json.loads(proc.stdout.strip())["id"] == 1

    def test_the_recording_is_reusable(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """The recorded file must be usable by another ``mock``: record once, use many."""
        target = tmp_path / "from.jsonl"
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"},
                },
            }
        )
        subprocess.run(
            self._from_command(target),
            input=request + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=60.0,
            cwd=ROOT,
        )
        assert target.exists()

        # Second time: do not touch the original server, only use the file.
        proc = run_mcpdump(["ls", _mock_command(target), "--quiet-server", "--json"], mcpdump_env)

        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["tools"], "the reused recording has no tools"

    def test_the_recording_covers_all_three_list_calls(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """What is recorded must include tools / resources / prompts — a mock that recorded
        only an ``initialize`` starts up, but the client hits a wall on its first call."""
        target = tmp_path / "from.jsonl"
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"},
                },
            }
        )
        subprocess.run(
            self._from_command(target),
            input=request + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=60.0,
            cwd=ROOT,
        )

        recorded = target.read_text(encoding="utf-8")
        for method in ("tools/list", "resources/list", "prompts/list"):
            assert method in recorded, f"did not record {method}"

    def test_it_says_where_the_recording_went(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """The recording is an artifact the user has to be able to find again."""
        target = tmp_path / "from.jsonl"
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "1"},
                },
            }
        )
        proc = subprocess.run(
            self._from_command(target),
            input=request + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=60.0,
            cwd=ROOT,
        )

        assert "from.jsonl" in proc.stderr

    def test_a_dead_server_fails_before_serving_anything(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        """When the original server cannot be reached, fail on the spot rather than start a
        hollow mock.

        An empty recording is refused at startup, but the worse case is a user believing the
        mock works while nothing is behind it.
        """
        target = tmp_path / "dead.jsonl"
        bogus = "definitely-not-a-real-executable-xyz"

        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "mcpdump",
                "mock",
                "--from",
                bogus,
                "--out",
                str(target),
            ],
            input="",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=mcpdump_env,
            timeout=60.0,
            cwd=ROOT,
        )

        assert proc.returncode != 0
        assert not target.exists() or target.stat().st_size == 0

    def test_file_and_from_together_are_refused(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        record = _record_session(tmp_path, mcpdump_env)
        proc = run_mcpdump(
            ["mock", str(record), "--from", "whatever"], mcpdump_env, timeout=20.0
        )
        assert proc.returncode != 0

    def test_neither_file_nor_from_is_refused(
        self, tmp_path: pathlib.Path, mcpdump_env: dict[str, str]
    ) -> None:
        proc = run_mcpdump(["mock"], mcpdump_env, timeout=20.0)
        assert proc.returncode != 0

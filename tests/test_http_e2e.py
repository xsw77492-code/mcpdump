"""End-to-end tests for the HTTP transport: CLI arguments through to the capability table.

``test_http_transport.py`` verifies the bytes; this verifies the whole chain runs, so bad
``build_transport`` / ``runtime`` wiring fails here while the transport units stay green.
"""

from __future__ import annotations

import json
import pathlib
import socket
import subprocess
import sys
from collections.abc import Iterator

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTTP_ECHO_SERVER = ROOT / "tests" / "fixtures" / "http_echo_server.py"


def _free_port() -> int:
    """Ask for a port nobody is using right now.

    Bind, then close; the tiny race window in between is good enough for a test, and
    simpler than a fixed port (which collides elsewhere) or parsing ``--port 0`` back.
    """
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


class _Fixture:
    """An HTTP server running on a background thread."""

    def __init__(self, mode: str = "json") -> None:
        self.port = _free_port()
        self.mode = mode
        self.url = f"http://127.0.0.1:{self.port}/mcp"
        self.proc: subprocess.Popen[str] | None = None

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [
                sys.executable,
                str(HTTP_ECHO_SERVER),
                "--port",
                str(self.port),
                "--mode",
                self.mode,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        # The fixture prints "host:port" to stdout on startup; that line confirms it is
        # really up, which beats sleeping for a guessed number of seconds.
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline().strip()
        assert line == f"127.0.0.1:{self.port}", (
            f"the fixture did not start up as expected: {line!r}"
        )

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait(timeout=5)
            self.proc = None


@pytest.fixture(params=["json", "sse"])
def http_fixture(request: pytest.FixtureRequest) -> Iterator[_Fixture]:
    """Run both response shapes.

    Parameterised because SSE is this project's own parser; testing JSON alone would leave
    the ``_iter_sse_messages`` path untravelled at the end-to-end level.
    """
    fixture = _Fixture(mode=request.param)
    fixture.start()
    try:
        yield fixture
    finally:
        fixture.stop()


def _mcpdump(
    args: list[str], env: dict[str, str], timeout: float = 60.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcpdump", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
        cwd=ROOT,
    )


class TestListOverHttp:
    """``mcpdump ls <url>`` completes the handshake and capability discovery."""

    def test_ls_discovers_all_capabilities(
        self, http_fixture: _Fixture, mcpdump_env: dict[str, str]
    ) -> None:
        result = _mcpdump(["ls", http_fixture.url, "--json"], mcpdump_env)

        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        assert payload["server"]["name"] == "echo-server"

        names = {tool["name"] for tool in payload["tools"]}
        assert names == {"echo", "add", "boom"}
        assert [r["uri"] for r in payload["resources"]] == ["echo://readme"]
        assert [p["name"] for p in payload["prompts"]] == ["greet"]

    def test_the_transport_is_reported_as_http(
        self, http_fixture: _Fixture, mcpdump_env: dict[str, str]
    ) -> None:
        """The panel must say this is an HTTP connection, not a subprocess."""
        result = _mcpdump(["ls", http_fixture.url], mcpdump_env)

        assert result.returncode == 0
        assert "Streamable HTTP" in result.stdout

    def test_a_tool_call_round_trips(
        self, http_fixture: _Fixture, mcpdump_env: dict[str, str]
    ) -> None:
        result = _mcpdump(
            ["call", http_fixture.url, "echo", "--args", '{"text":"over http"}'],
            mcpdump_env,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "over http" in result.stdout


class TestHttpErrorsAreClear:
    """When it cannot connect, it must say which address it tried."""

    def test_an_unreachable_port_names_the_address(self, mcpdump_env: dict[str, str]) -> None:
        """With nothing listening on the port, the error must carry the address it tried."""
        port = _free_port()  # nothing listening
        result = _mcpdump(["ls", f"http://127.0.0.1:{port}/mcp"], mcpdump_env)

        assert result.returncode != 0
        assert f"127.0.0.1:{port}" in result.stdout + result.stderr


class TestLocalhostNeverGoesThroughAProxy:
    """A loopback address must not go through ``HTTP_PROXY``.

    A dev box usually sets ``HTTP_PROXY`` and not ``no_proxy``: urllib then sends the
    request to the proxy, which answers 502, and the user debugs a server never contacted.
    """

    def test_a_loopback_request_bypasses_the_proxy_environment(
        self, http_fixture: _Fixture, mcpdump_env: dict[str, str]
    ) -> None:
        env = {**mcpdump_env, "HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9"}
        env.pop("no_proxy", None)
        env.pop("NO_PROXY", None)

        result = _mcpdump(["ls", http_fixture.url, "--json"], env)

        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["server"]["name"] == "echo-server"

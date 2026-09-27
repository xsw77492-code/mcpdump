"""Pure-function tests for ``runtime.py``.

Only the parts that need no real process; assembly and lifecycle are covered end-to-end
by ``test_cli.py``.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from mcpdump.runtime import SessionOptions, abbreviate_home, open_session


def test_abbreviates_the_posix_form_of_home() -> None:
    home = os.path.expanduser("~")
    assert abbreviate_home(f"{home}/projects/server.py") == "~/projects/server.py"


def test_abbreviates_the_backslash_form_of_home() -> None:
    """Windows ``expanduser`` gives backslashes, while user input often uses forward slashes;
    checking only one form makes the abbreviation fail silently.
    """
    home = os.path.expanduser("~")
    assert abbreviate_home(f"{home}\\projects\\server.py") == "~\\projects\\server.py"


def test_leaves_other_paths_alone() -> None:
    assert abbreviate_home("/opt/servers/thing.py") == "/opt/servers/thing.py"
    assert abbreviate_home("npx -y @acme/weather-mcp") == "npx -y @acme/weather-mcp"


class _FakeTransport:
    """A dumb transport exposing only the few methods ``MCPSession`` uses.

    ``recv`` must answer the handshake: entering ``open_session`` runs ``initialize``, and
    returning ``None`` would time out after the default 30 seconds instead of testing here.
    """

    def start(self) -> None: ...

    def send(self, line: str) -> None: ...

    def recv(self, timeout: float | None = None) -> str | None:
        return json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "serverInfo": {"name": "fake", "version": "0"},
            },
        })

    def close(self) -> None: ...


def _spy(seen: dict[str, Any]) -> Any:
    """Build a ``build_transport`` stand-in that records the arguments it was called with."""

    def _fake(server: Any, **kwargs: Any) -> _FakeTransport:
        seen["server"] = server
        seen.update(kwargs)
        return _FakeTransport()

    return _fake


class TestTransportWiring:
    """``open_session`` hands the options to the transport correctly.

    ``build_transport``'s ``timeout`` has a default, and the stdio and HTTP timeouts are
    separate knobs, so a missing one reports no error and just quietly stops working.
    """

    def test_the_timeout_reaches_the_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}
        monkeypatch.setattr("mcpdump.runtime.build_transport", _spy(seen))
        opts = SessionOptions(server="https://example.com/mcp", timeout=7.5)
        with open_session(opts):
            pass

        assert seen["timeout"] == 7.5

    def test_the_launch_spec_is_what_the_transport_sees(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What is passed must be ``launch_spec``: argv if present, else the server string."""
        seen: dict[str, Any] = {}
        monkeypatch.setattr("mcpdump.runtime.build_transport", _spy(seen))
        opts = SessionOptions(server="ignored", argv=["python", "server.py"])
        with open_session(opts):
            pass

        assert seen["server"] == ["python", "server.py"]

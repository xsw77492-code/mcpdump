"""End-to-end tests for ``mcpdump discover``.

Everything goes through a subprocess. Two things are pinned repeatedly: secrets never leak
into any output channel, and "unavailable" is not the same as "should not connect".
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from conftest import DEMO_SERVER_ARGS, ROOT, FakeHome
from mcpdump.exits import EXIT_OK, EXIT_USAGE

#: A string that "looks like a real secret". Appearing in any output is a failure.
SECRET = "ghp_literalsecret123"


def _env(fake_home: FakeHome, **extra: str) -> dict[str, str]:
    """The subprocess environment: a fake home plus pinned language, ASCII and width.

    Width must be pinned, or rendering follows the terminal and assertions drift.
    """
    return fake_home.env(MCPDUMP_LANG="en", MCPDUMP_ASCII="1", COLUMNS="100", **extra)


def _run(
    args: list[str],
    env: dict[str, str],
    timeout: float = 60.0,
) -> subprocess.CompletedProcess[str]:
    """Run mcpdump once, with stdin explicitly wired to the null device.

    ``discover`` stops in a terminal and waits for the user to pick; wiring stdin to the null
    device tests "must not block when non-interactive", which would otherwise hang.
    """
    return subprocess.run(
        [sys.executable, "-m", "mcpdump", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
        cwd=ROOT,
    )


def _populate(fake_home: FakeHome, **extra_servers: dict[str, Any]) -> None:
    """Place a Cursor config in the fake home: one that really runs, one written wrong.

    ``broken`` is deliberate. Commands use ``sys.executable`` rather than ``npx``, whose
    presence on PATH depends on the machine.
    """
    servers: dict[str, Any] = {
        "echo": {"command": sys.executable, "args": list(DEMO_SERVER_ARGS)},
        "broken": {"args": ["--oops"]},
    }
    servers.update(extra_servers)
    fake_home.write(".cursor/mcp.json", {"mcpServers": servers})


def _discover(
    fake_home: FakeHome, *args: str, timeout: float = 60.0
) -> subprocess.CompletedProcess[str]:
    """Run discover once with ``--project`` — the workspace config must land in the
    fake directory."""
    return _run(
        ["discover", *args, "--project", str(fake_home.project)],
        _env(fake_home),
        timeout=timeout,
    )


# ---------------------------------------------------------------- bare invocation


class TestBareInvocation:
    def test_lists_what_is_already_configured(self, fake_home: FakeHome) -> None:
        """Right after installing, the first thing you want is "what do I already have"; a
        bare run should give exactly that.
        """
        _populate(fake_home)

        proc = _run([], _env(fake_home))

        assert proc.returncode == EXIT_OK, proc.stderr
        assert "echo" in proc.stdout
        assert "Usage:" not in proc.stdout  # with a server present, do not dump help

    def test_falls_back_to_help_when_nothing_is_configured(self, fake_home: FakeHome) -> None:
        """With not a single server found, help is what the user actually needs."""
        proc = _run([], _env(fake_home))

        assert proc.returncode == EXIT_OK
        assert "Usage:" in proc.stdout

    def test_does_not_block_when_stdin_is_not_a_terminal(self, fake_home: FakeHome) -> None:
        _populate(fake_home)

        proc = _run([], _env(fake_home), timeout=30.0)

        assert proc.returncode == EXIT_OK, proc.stderr

    def test_the_bare_view_says_availability_was_not_probed(self, fake_home: FakeHome) -> None:
        _populate(fake_home)

        proc = _run([], _env(fake_home))

        assert "static checks only" in proc.stdout


# ---------------------------------------------------------------- JSON channel


class TestJsonOutput:
    def test_json_is_the_only_thing_on_stdout(self, fake_home: FakeHome) -> None:
        """Machine-readable output cannot carry decoration; one banner line breaks the
        parser.
        """
        _populate(fake_home)

        proc = _discover(fake_home, "--json")

        assert proc.returncode == EXIT_OK, proc.stderr
        json.loads(proc.stdout)

    def test_schema_is_stable(self, fake_home: FakeHome) -> None:
        _populate(fake_home)

        payload = json.loads(_discover(fake_home, "--json").stdout)

        assert set(payload) == {"clients", "servers"}
        client = next(c for c in payload["clients"] if c["client"] == "cursor")
        assert {"client", "clientName", "path", "found", "serverCount", "issues"} <= set(client)
        assert client["found"] is True
        server = next(s for s in payload["servers"] if s["name"] == "echo")
        assert {"name", "client", "origin", "transport", "availability", "envKeys"} <= set(server)
        assert server["availability"] == "ready"

    def test_a_broken_entry_shows_up_as_an_issue_not_as_silence(self, fake_home: FakeHome) -> None:
        _populate(fake_home)

        payload = json.loads(_discover(fake_home, "--json").stdout)

        cursor = next(c for c in payload["clients"] if c["client"] == "cursor")
        assert [i["problem"] for i in cursor["issues"]] == ["entry-invalid"]
        assert cursor["issues"][0]["detail"] == "broken @ mcpServers"
        assert [s["name"] for s in payload["servers"]] == ["echo"]

    def test_secrets_never_reach_the_payload(self, fake_home: FakeHome) -> None:
        """This is the hardest constraint of the feature: config files are full of
        secrets."""
        fake_home.write(
            ".cursor/mcp.json",
            {
                "mcpServers": {
                    "s": {
                        "command": sys.executable,
                        "env": {"GITHUB_TOKEN": SECRET},
                        "headers": {"Authorization": f"Bearer {SECRET}"},
                    }
                }
            },
        )

        proc = _discover(fake_home, "--json")

        assert SECRET not in proc.stdout
        payload = json.loads(proc.stdout)
        assert payload["servers"][0]["envKeys"] == ["GITHUB_TOKEN"]
        assert payload["servers"][0]["headerKeys"] == ["Authorization"]

    def test_secrets_never_reach_the_human_view_either(self, fake_home: FakeHome) -> None:
        fake_home.write(
            ".cursor/mcp.json",
            {"mcpServers": {"s": {"command": sys.executable, "env": {"TOKEN": SECRET}}}},
        )

        proc = _discover(fake_home)

        assert SECRET not in proc.stdout
        assert SECRET not in proc.stderr


# ---------------------------------------------------------------- filtering


class TestClientFilter:
    def test_narrows_the_scan(self, fake_home: FakeHome) -> None:
        _populate(fake_home)
        fake_home.write(
            ".continue/config.json",
            {"mcpServers": [{"name": "mem", "command": sys.executable}]},
        )

        proc = _discover(fake_home, "--client", "cursor")

        assert proc.returncode == EXIT_OK, proc.stderr
        assert "echo" in proc.stdout
        assert "mem" not in proc.stdout

    def test_accepts_the_display_name_too(self, fake_home: FakeHome) -> None:
        """``--client "Claude Desktop"`` and ``--client claude-desktop`` must both be
        accepted.
        """
        _populate(fake_home)

        proc = _discover(fake_home, "--client", "Cursor")

        assert proc.returncode == EXIT_OK, proc.stderr
        assert "echo" in proc.stdout

    def test_an_unknown_client_is_a_usage_error(self, fake_home: FakeHome) -> None:
        _populate(fake_home)

        proc = _discover(fake_home, "--client", "nope")

        assert proc.returncode == EXIT_USAGE
        assert "nope" in proc.stderr
        assert "cursor" in proc.stderr  # and list the available ones while at it

    def test_disambiguates_use(self, fake_home: FakeHome) -> None:
        """When ``--use`` reports an ambiguity, ``--client`` is the fix; the two must
        compose.
        """
        dup = {"command": sys.executable, "args": list(DEMO_SERVER_ARGS)}
        fake_home.write(".cursor/mcp.json", {"mcpServers": {"dup": dup}})
        fake_home.write(
            ".continue/config.json", {"mcpServers": [{"name": "dup", **dup}]}
        )

        proc = _discover(fake_home, "--use", "dup", "--client", "cursor", timeout=90.0)

        assert proc.returncode == EXIT_OK, proc.stderr
        assert "echo-server" in proc.stdout


# ---------------------------------------------------------------- connecting


class TestUseOption:
    def test_connects_and_renders_the_capability_tables(self, fake_home: FakeHome) -> None:
        """The whole point of ``--use``: connect without retyping the launch command."""
        _populate(fake_home)

        proc = _discover(fake_home, "--use", "echo", timeout=90.0)

        assert proc.returncode == EXIT_OK, proc.stderr
        assert "echo-server" in proc.stdout
        assert "greet" in proc.stdout  # prompts too, so the whole handshake ran

    def test_connects_even_when_static_checks_flagged_it(self, fake_home: FakeHome) -> None:
        """Static checks can only hint, not veto: the environment variable may be set only in
        an interactive shell.
        """
        fake_home.write(
            ".cursor/mcp.json",
            {
                "mcpServers": {
                    "echo": {
                        "command": sys.executable,
                        "args": list(DEMO_SERVER_ARGS),
                        "env": {"UNUSED": "${MCPDUMP_TEST_UNSET_XYZ}"},
                    }
                }
            },
        )

        proc = _discover(fake_home, "--use", "echo", timeout=90.0)

        assert proc.returncode == EXIT_OK, proc.stderr
        assert "echo-server" in proc.stdout

    def test_an_unknown_name_is_a_usage_error(self, fake_home: FakeHome) -> None:
        _populate(fake_home)

        proc = _discover(fake_home, "--use", "nope")

        assert proc.returncode == EXIT_USAGE
        assert "nope" in proc.stderr

    def test_an_ambiguous_name_asks_for_a_client_instead_of_guessing(
        self, fake_home: FakeHome
    ) -> None:
        """A server of the same name in two clients is the norm.

        Guessing wrong would connect where the user did not expect, while the error just asks
        for one more ``--client``.
        """
        dup = {"command": sys.executable}
        fake_home.write(".cursor/mcp.json", {"mcpServers": {"dup": dup}})
        fake_home.write(".continue/config.json", {"mcpServers": [{"name": "dup", **dup}]})

        proc = _discover(fake_home, "--use", "dup")

        assert proc.returncode == EXIT_USAGE
        assert "appears in 2 clients" in proc.stderr


# ---------------------------------------------------------------- probing


class TestProbe:
    def test_upgrades_a_working_server_to_verified(self, fake_home: FakeHome) -> None:
        _populate(fake_home)

        proc = _discover(fake_home, "--probe", timeout=120.0)

        assert proc.returncode == EXIT_OK, proc.stderr
        assert "verified" in proc.stdout

    def test_announces_what_it_is_doing(self, fake_home: FakeHome) -> None:
        """Probing a server can take 10 seconds; without a word the user would think it froze."""
        _populate(fake_home)

        proc = _discover(fake_home, "--probe", timeout=120.0)

        assert "probing echo" in proc.stderr

    def test_does_not_probe_what_static_checks_already_rejected(self, fake_home: FakeHome) -> None:
        """Connecting to a stdio entry that already fails statically hides the real cause: the
        user sees "timeout" when the command does not exist.
        """
        fake_home.write(
            ".cursor/mcp.json",
            {"mcpServers": {"ghost": {"command": "definitely-not-a-real-binary-xyz"}}},
        )

        proc = _discover(fake_home, "--probe", timeout=120.0)

        assert proc.returncode == EXIT_OK, proc.stderr
        assert "command not found" in proc.stdout
        assert "probing ghost" not in proc.stderr

    def test_the_static_note_disappears_once_probing_happened(self, fake_home: FakeHome) -> None:
        _populate(fake_home)

        plain = _discover(fake_home)
        probed = _discover(fake_home, "--probe", timeout=120.0)

        assert "static checks only" in plain.stdout
        assert "static checks only" not in probed.stdout


# ---------------------------------------------------------------- problem reporting


class TestProblemReporting:
    def test_a_broken_entry_names_the_client_and_the_file(self, fake_home: FakeHome) -> None:
        """Reporting only "1 config could not be fully read" says nothing; the user needs to
        match it to a concrete entry.
        """
        _populate(fake_home)

        proc = _discover(fake_home)

        assert "Cursor · mcp.json" in proc.stdout
        assert "broken @ mcpServers" in proc.stdout
        assert "neither a usable command nor a url" in proc.stdout

    def test_the_count_matches_what_is_listed(self, fake_home: FakeHome) -> None:
        """The header prints once and details list one by one; repeating the header per entry
        would make the count wrong.
        """
        _populate(fake_home)
        fake_home.write(
            ".continue/config.json", {"mcpServers": [{"name": "no-cmd", "args": []}]}
        )

        proc = _discover(fake_home)

        assert proc.stdout.count("could not be read") == 1
        assert "2 config entry/entries could not be read" in proc.stdout

    def test_uncertain_paths_are_disclosed(self, fake_home: FakeHome) -> None:
        """Say when a client's path comes from community reports, or the user thinks "not
        found" is their own fault.
        """
        fake_home.write(
            ".continue/config.json",
            {"mcpServers": [{"name": "mem", "command": sys.executable}]},
        )

        proc = _discover(fake_home)

        assert "community reports" in proc.stdout

    def test_a_jsonc_config_is_read_not_rejected(self, fake_home: FakeHome) -> None:
        """VS Code's ``mcp.json`` allows comments and trailing commas; calling it "corrupt" is
        a false alarm.
        """
        fake_home.write(
            ".cursor/mcp.json",
            """{
  // 我的文件系统
  "mcpServers": {
    "fs": {"command": "node", "args": ["a.js"],},
  },
}
""",
        )

        proc = _discover(fake_home)

        assert "fs" in proc.stdout
        assert "could not be read" not in proc.stdout


# ---------------------------------------------------------------- empty state and help


class TestEmptyStateAndHelp:
    def test_no_configs_still_points_somewhere(self, fake_home: FakeHome) -> None:
        """mcpdump does not depend on client config, and that must be said on the spot, or the
        user sees no way out.
        """
        proc = _discover(fake_home)

        assert proc.returncode == EXIT_OK
        assert "No MCP server configuration found" in proc.stdout
        assert "mcpdump ls" in proc.stdout

    def test_the_hints_never_echo_a_full_launch_command(self, fake_home: FakeHome) -> None:
        """Echoing the full launch command is a trap: its arguments already carry quotes, and
        wrapping them once more guarantees a wrong paste.
        """
        _populate(fake_home)

        proc = _discover(fake_home)

        assert "mcpdump discover --use echo" in proc.stdout
        assert " ".join(DEMO_SERVER_ARGS) not in proc.stdout

    def test_help_lists_every_option(self, fake_home: FakeHome) -> None:
        proc = _run(["discover", "--help"], _env(fake_home))

        assert proc.returncode == EXIT_OK
        for flag in ("--use", "--client", "--project", "--probe", "--json"):
            assert flag in proc.stdout

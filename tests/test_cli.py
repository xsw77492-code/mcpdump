"""CLI end-to-end tests.

Run ``python -m mcpdump ...`` in a real subprocess and assert exit code, stdout/stderr and
that ``--json`` parses; the thin commands a user actually types must not fall over.
"""

from __future__ import annotations

import json

from conftest import run_mcpdump


def test_ls_json_is_machine_parseable(echo_server_cmd: str, mcpdump_env: dict[str, str]) -> None:
    proc = run_mcpdump(["ls", echo_server_cmd, "--json"], mcpdump_env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)  # must parse strictly
    assert payload["server"]["name"] == "echo-server"
    assert {t["name"] for t in payload["tools"]} == {"echo", "add", "boom"}
    assert payload["resources"][0]["uri"] == "echo://readme"


def test_ls_renders_human_output(echo_server_cmd: str, mcpdump_env: dict[str, str]) -> None:
    proc = run_mcpdump(["ls", echo_server_cmd, "--quiet-server"], mcpdump_env)
    assert proc.returncode == 0
    assert "echo-server" in proc.stdout
    assert "echo" in proc.stdout
    assert "Tools (3)" in proc.stdout


def test_ls_prints_next_steps_with_shortened_server(
    echo_server_cmd: str, mcpdump_env: dict[str, str]
) -> None:
    """A launch command that is too long is replaced by a placeholder, so the hint does not
    wrap.
    """
    proc = run_mcpdump(["ls", echo_server_cmd, "--quiet-server"], mcpdump_env)
    assert proc.returncode == 0
    assert "Next" in proc.stdout
    assert "mcpdump call <SERVER> echo --args '{}'" in proc.stdout


def test_ls_does_not_print_next_steps_in_json_mode(
    echo_server_cmd: str, mcpdump_env: dict[str, str]
) -> None:
    """The hint block is for humans and must not pollute --json's byte-stable output."""
    proc = run_mcpdump(["ls", echo_server_cmd, "--json"], mcpdump_env)
    assert proc.returncode == 0
    assert "Next" not in proc.stdout
    json.loads(proc.stdout)


def test_ls_speaks_chinese_when_mcpdump_lang_is_zh(
    echo_server_cmd: str, mcpdump_env_zh: dict[str, str]
) -> None:
    """MCPDUMP_LANG=zh switches every user-facing string, and no English leaks through."""
    proc = run_mcpdump(["ls", echo_server_cmd, "--quiet-server"], mcpdump_env_zh)
    assert proc.returncode == 0, proc.stderr
    assert "工具（3）" in proc.stdout
    assert "下一步" in proc.stdout
    assert "Tools (3)" not in proc.stdout
    assert "Next" not in proc.stdout


def test_help_text_follows_mcpdump_lang(
    mcpdump_env: dict[str, str], mcpdump_env_zh: dict[str, str]
) -> None:
    """Typer evaluates help= at import time, so MCPDUMP_LANG must be set before the process
    starts.
    """
    en = run_mcpdump(["--help"], mcpdump_env)
    zh = run_mcpdump(["--help"], mcpdump_env_zh)
    assert en.returncode == 0, en.stderr
    assert zh.returncode == 0, zh.stderr
    assert "List the tools" in en.stdout
    assert "列出服务端暴露的工具" in zh.stdout
    assert "列出服务端暴露的工具" not in en.stdout


def test_call_json_contains_wire_traffic(echo_server_cmd: str, mcpdump_env: dict[str, str]) -> None:
    proc = run_mcpdump(
        ["call", echo_server_cmd, "echo", "--args", '{"text":"hi"}', "--json"],
        mcpdump_env,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["result"]["content"][0]["text"] == "hi"
    methods = [ex["method"] for ex in payload["exchanges"]]
    assert "tools/call" in methods
    assert any(ex["request"].startswith('{"jsonrpc":"2.0"') for ex in payload["exchanges"])


def test_call_unknown_tool_exits_1_with_suggestion(
    echo_server_cmd: str, mcpdump_env: dict[str, str]
) -> None:
    proc = run_mcpdump(["call", echo_server_cmd, "echoo", "--no-wire"], mcpdump_env)
    assert proc.returncode == 1
    assert "Did you mean:" in proc.stderr
    assert "echo" in proc.stderr


def test_call_wire_view_shows_frames_and_summary(
    echo_server_cmd: str, mcpdump_env: dict[str, str]
) -> None:
    proc = run_mcpdump(
        ["call", echo_server_cmd, "echo", "--args", '{"text":"hi"}', "--quiet-server"],
        mcpdump_env,
    )
    assert proc.returncode == 0, proc.stderr
    assert "→ tools/call" in proc.stdout
    assert "← tools/call" in proc.stdout
    assert "round trips" in proc.stdout
    assert "→ initialize" in proc.stdout


def test_call_payload_brackets_are_not_parsed_as_markup(
    echo_server_cmd: str, mcpdump_env: dict[str, str]
) -> None:
    """A payload containing a JSON array (``[`` ``]``) must render verbatim, not as markup tags."""
    args = json.dumps({"text": "[bold red]hi[/]"})
    proc = run_mcpdump(
        ["call", echo_server_cmd, "echo", "--args", args, "--quiet-server"], mcpdump_env
    )
    assert proc.returncode == 0, proc.stderr
    assert "[bold red]hi[/]" in proc.stdout


def test_call_no_wire_hides_raw_frames(echo_server_cmd: str, mcpdump_env: dict[str, str]) -> None:
    args = ["call", echo_server_cmd, "echo", "--args", '{"text":"hi"}', "--no-wire"]
    proc = run_mcpdump([*args, "--quiet-server"], mcpdump_env)
    assert proc.returncode == 0, proc.stderr
    assert "← tools/call" not in proc.stdout
    assert "round trips" not in proc.stdout


def test_call_full_flag_keeps_long_payload(
    echo_server_cmd: str, mcpdump_env: dict[str, str]
) -> None:
    """--full does not truncate, so its output must be longer than the default mode's."""
    args = json.dumps({"text": "中" * 200})
    base = ["call", echo_server_cmd, "echo", "--args", args, "--quiet-server"]
    truncated = run_mcpdump(base, mcpdump_env)
    full = run_mcpdump([*base, "--full"], mcpdump_env)
    assert truncated.returncode == 0, truncated.stderr
    assert full.returncode == 0, full.stderr
    assert len(full.stdout) > len(truncated.stdout)


def test_call_unknown_tool_with_brackets_does_not_crash(
    echo_server_cmd: str, mcpdump_env: dict[str, str]
) -> None:
    """The tool name comes from user input; ``[`` ``]`` in it must not break markup parsing."""
    proc = run_mcpdump(["call", echo_server_cmd, "[bold]x[/]", "--no-wire"], mcpdump_env)
    assert proc.returncode == 1
    assert "[bold]x[/]" in proc.stderr


def test_call_rejects_invalid_json_args(echo_server_cmd: str, mcpdump_env: dict[str, str]) -> None:
    proc = run_mcpdump(["call", echo_server_cmd, "echo", "--args", "not-json"], mcpdump_env)
    assert proc.returncode == 1
    assert "is not valid JSON" in proc.stderr


def test_call_rejects_non_object_args(echo_server_cmd: str, mcpdump_env: dict[str, str]) -> None:
    proc = run_mcpdump(["call", echo_server_cmd, "echo", "--args", "[1,2]"], mcpdump_env)
    assert proc.returncode == 1
    assert "must be a JSON object" in proc.stderr


def test_call_surfaces_server_error_as_exit_2(
    echo_server_cmd: str, mcpdump_env: dict[str, str]
) -> None:
    """A JSON-RPC error from the server must exit 2, not 0."""
    proc = run_mcpdump(["call", echo_server_cmd, "nope", "--no-wire"], mcpdump_env)
    # nope is not in tools/list, so it is caught locally as a usage error (1)
    assert proc.returncode == 1


def test_version_flag(mcpdump_env: dict[str, str]) -> None:
    proc = run_mcpdump(["--version"], mcpdump_env)
    assert proc.returncode == 0
    assert proc.stdout.strip().startswith("mcpdump ")


def test_help_lists_core_commands(mcpdump_env: dict[str, str]) -> None:
    proc = run_mcpdump(["--help"], mcpdump_env)
    assert proc.returncode == 0
    assert "ls" in proc.stdout
    assert "call" in proc.stdout

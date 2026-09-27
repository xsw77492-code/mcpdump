"""End-to-end tests for ``mcpdump check``.

Run it in a real subprocess and assert exit code, stdout/stderr and the byte shape of the
machine channels; the in-process ``test_checks.py`` never sees an exit code.
"""

from __future__ import annotations

import json
import re

from conftest import ServerCommand, run_mcpdump

#: Terminal escape sequences. ``--badge`` output pastes into a README with none.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


class TestExitCodes:
    def test_passes_on_the_example_server(
        self, echo_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        proc = run_mcpdump(["check", echo_server_cmd, "--quiet-server"], mcpdump_env)
        assert proc.returncode == 0, proc.stderr
        assert "10 checks" in proc.stdout
        assert "All 10 checks passed." in proc.stdout

    def test_exits_2_when_anything_is_not_conformant(
        self, broken_server: ServerCommand, mcpdump_env: dict[str, str]
    ) -> None:
        """Exit 2 on any violation. CI only reads this number."""
        proc = run_mcpdump(["check", broken_server("all"), "--quiet-server"], mcpdump_env)
        assert proc.returncode == 2

    def test_output_switches_are_mutually_exclusive(
        self, echo_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        """Only one of the three output channels may be used.

        The error must name exactly what the user passed, or adding a third channel would
        miss a spot or blame the wrong pair.
        """
        proc = run_mcpdump(
            ["check", echo_server_cmd, "--badge", "--json", "--markdown"], mcpdump_env
        )
        assert proc.returncode == 1
        assert "`--json`, `--badge`, `--markdown` cannot be combined." in proc.stderr

    def test_mutual_exclusion_names_only_what_was_passed(
        self, echo_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        proc = run_mcpdump(["check", echo_server_cmd, "--json", "--markdown"], mcpdump_env)
        assert proc.returncode == 1
        assert "`--json`, `--markdown` cannot be combined." in proc.stderr
        assert "--badge" not in proc.stderr


class TestHumanReport:
    def test_failures_carry_spec_and_evidence(
        self, broken_server: ServerCommand, mcpdump_env: dict[str, str]
    ) -> None:
        proc = run_mcpdump(["check", broken_server("stdout-banner"), "--quiet-server"], mcpdump_env)
        assert "stdout-purity" in proc.stdout
        assert "spec:" in proc.stdout
        assert "evidence:" in proc.stdout

    def test_passing_checks_take_one_line_each(
        self, echo_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        """Listing every passing check is noise; the value of the report is in the failures."""
        proc = run_mcpdump(["check", echo_server_cmd, "--quiet-server"], mcpdump_env)
        assert "Conformance report" in proc.stdout
        assert "spec:" not in proc.stdout
        assert "evidence:" not in proc.stdout

    def test_speaks_chinese_when_mcpdump_lang_is_zh(
        self, echo_server_cmd: str, mcpdump_env_zh: dict[str, str]
    ) -> None:
        proc = run_mcpdump(["check", echo_server_cmd, "--quiet-server"], mcpdump_env_zh)
        assert proc.returncode == 0, proc.stderr
        assert "一致性报告" in proc.stdout
        assert "全部 10 项检查通过。" in proc.stdout
        assert "Conformance report" not in proc.stdout


class TestJsonChannel:
    def test_is_machine_parseable(
        self, broken_server: ServerCommand, mcpdump_env: dict[str, str]
    ) -> None:
        proc = run_mcpdump(["check", broken_server("stdout-banner"), "--json"], mcpdump_env)
        assert proc.returncode == 2
        payload = json.loads(proc.stdout)
        assert payload["summary"]["score"] == "9/10"
        assert payload["summary"]["conformant"] is False
        assert payload["server"]["name"] == "broken-server"

    def test_does_not_leak_the_human_report(
        self, echo_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        proc = run_mcpdump(["check", echo_server_cmd, "--json"], mcpdump_env)
        assert "Conformance report" not in proc.stdout
        assert "Next" not in proc.stdout
        json.loads(proc.stdout)


class TestBadgeChannel:
    def test_is_paste_ready_markdown(
        self, echo_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        """``--badge`` prints Markdown that pastes straight in, with a source parameter
        on the link.
        """
        proc = run_mcpdump(["check", echo_server_cmd, "--badge"], mcpdump_env)
        assert proc.returncode == 0, proc.stderr
        line = proc.stdout.strip()
        assert line.startswith("[![")
        assert line.endswith(")")
        assert "\n" not in line
        assert not ANSI.search(line)
        assert "img.shields.io" in line
        assert "utm_source=badge" in line

    def test_color_tracks_the_score(
        self, broken_server: ServerCommand, mcpdump_env: dict[str, str]
    ) -> None:
        perfect = run_mcpdump(["check", broken_server("none"), "--badge"], mcpdump_env)
        assert "10%2F10-brightgreen" in perfect.stdout

        good = run_mcpdump(["check", broken_server("stdout-banner"), "--badge"], mcpdump_env)
        assert "9%2F10-green" in good.stdout

        bad = run_mcpdump(["check", broken_server("all"), "--badge"], mcpdump_env)
        assert bad.stdout.strip().endswith("red)") or "-red)" in bad.stdout

    def test_still_prints_when_not_conformant(
        self, broken_server: ServerCommand, mcpdump_env: dict[str, str]
    ) -> None:
        """Printed even when failing: a red badge is the motivation to fix things."""
        proc = run_mcpdump(["check", broken_server("all"), "--badge"], mcpdump_env)
        assert proc.returncode == 2
        assert proc.stdout.strip().startswith("[![")


class TestMarkdownChannel:
    """The byte shape of ``--markdown``: GitHub parses it, a human does not.

    The fine-grained structure (collapsing, fence length, ordering) is asserted in
    ``test_markdown.py``, where a report is built directly without a subprocess.
    """

    def test_is_paste_ready_markdown(
        self, echo_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        proc = run_mcpdump(["check", echo_server_cmd, "--markdown"], mcpdump_env)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.startswith("### ")
        assert not ANSI.search(proc.stdout)
        assert "Conformance report" not in proc.stdout

    def test_ends_with_exactly_one_newline(
        self, echo_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        """One extra newline and GitHub leaves a blank line at the end of the comment."""
        proc = run_mcpdump(["check", echo_server_cmd, "--markdown"], mcpdump_env)
        assert proc.stdout.endswith("\n")
        assert not proc.stdout.endswith("\n\n")

    def test_failures_are_expanded_passes_are_collapsed(
        self, broken_server: ServerCommand, mcpdump_env: dict[str, str]
    ) -> None:
        """The value of a PR comment is entirely in which checks failed; listing the
        passing ones pushes the failures out of view.
        """
        proc = run_mcpdump(["check", broken_server("stdout-banner"), "--markdown"], mcpdump_env)
        assert proc.returncode == 2
        assert "#### Failed (1)" in proc.stdout
        assert "stdout-purity" in proc.stdout
        assert "spec:" in proc.stdout
        assert "<details><summary>evidence</summary>" in proc.stdout
        assert "<details><summary>Show 9 passing check(s)</summary>" in proc.stdout

    def test_carries_the_badge_so_the_comment_is_self_contained(
        self, echo_server_cmd: str, mcpdump_env: dict[str, str]
    ) -> None:
        proc = run_mcpdump(["check", echo_server_cmd, "--markdown"], mcpdump_env)
        assert "img.shields.io" in proc.stdout

    def test_speaks_chinese_when_mcpdump_lang_is_zh(
        self, echo_server_cmd: str, mcpdump_env_zh: dict[str, str]
    ) -> None:
        proc = run_mcpdump(["check", echo_server_cmd, "--markdown"], mcpdump_env_zh)
        assert proc.returncode == 0, proc.stderr
        assert "未通过" not in proc.stdout  # no "failed" section when everything passes
        assert "展开 10 项通过的检查" in proc.stdout
        assert "Show 10 passing check(s)" not in proc.stdout


class TestHelp:
    def test_lists_both_output_switches(self, mcpdump_env: dict[str, str]) -> None:
        proc = run_mcpdump(["check", "--help"], mcpdump_env)
        assert proc.returncode == 0
        assert "--badge" in proc.stdout
        assert "--json" in proc.stdout
        assert "--markdown" in proc.stdout
        assert "--protocol-version" in proc.stdout

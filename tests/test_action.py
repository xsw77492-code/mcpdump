"""Contract tests between ``action.yml`` and the CLI.

The Action only ever runs on GitHub's machines, so these pin the two things most likely
to rot quietly: every flag it passes must exist, and it must install this package.
"""

from __future__ import annotations

import re

from conftest import ROOT, run_mcpdump

ACTION = ROOT / "action.yml"
PYPROJECT = ROOT / "pyproject.toml"
READMES = (ROOT / "README.md", ROOT / "README.zh-CN.md")

# Only long flags count. ``-X`` / ``-f`` belong to ``gh api``, not to the CLI.
FLAG = re.compile(r"--[a-z][a-z0-9-]*")

# What is left after ``pip install`` once ``--flag`` arguments are stripped.
INSTALL = re.compile(r"pip install\s+(?:--\S+\s+)*(.+)$")


def _action_text() -> str:
    return ACTION.read_text(encoding="utf-8")


def _check_invocation() -> str:
    """The line in the Action that actually calls ``mcpdump check``."""
    for line in _action_text().splitlines():
        if line.strip().startswith("mcpdump check "):
            return line
    raise AssertionError("no mcpdump check invocation found in action.yml")


def _install_targets() -> list[str]:
    """Every package name the Action installs, with any version pin stripped."""
    targets: list[str] = []
    for line in _action_text().splitlines():
        match = INSTALL.search(line.strip())
        if match is None:
            continue
        targets.append(match.group(1).strip().strip('"').split("==")[0])
    return targets


def _package_name() -> str:
    match = re.search(r'^name = "(.+)"', PYPROJECT.read_text(encoding="utf-8"), re.M)
    assert match is not None, "no name in pyproject.toml"
    return match.group(1)


class TestActionMatchesTheCli:
    def test_it_calls_the_check_command(self) -> None:
        assert _check_invocation()

    def test_every_flag_it_passes_exists(self, mcpdump_env: dict[str, str]) -> None:
        used = set(FLAG.findall(_check_invocation()))
        assert used, "this line has no flags at all, so it must have been broken"
        documented = set(FLAG.findall(run_mcpdump(["check", "--help"], mcpdump_env).stdout))
        assert used <= documented


class TestActionInstallsThisPackage:
    def test_it_installs_the_package_this_repo_publishes(self) -> None:
        assert set(_install_targets()) == {_package_name()}

    def test_it_pins_the_version_when_asked_to(self) -> None:
        """An empty ``version`` installs latest, a given one pins it: both branches must stay."""
        assert f'"{_package_name()}==$MCPDUMP_VERSION"' in _action_text()


class TestReadmesStayInSync:
    def test_both_readmes_document_the_action(self) -> None:
        """Written into only one README, the other half of the readers cannot find it."""
        for path in READMES:
            assert f"xsw77492-code/{_package_name()}@v1" in path.read_text(encoding="utf-8"), (
                path.name
            )

"""Shared pytest fixtures.

Tests launch real server subprocesses (default: the bundled demo server) with the output
language pinned, else assertions follow the developer's locale and pass on CI.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable, Iterator

import pytest

from mcpdump import i18n
from mcpdump.runtime import SessionOptions, open_session
from mcpdump.services.runner import CheckReport, run_checks

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The bundled demo server as argv tokens after the interpreter (``python -m mcpdump.demo``).
#: Tests that embed it in a longer command line (``record`` / ``watch`` / ``mock``) splice
#: these tokens in; ``echo_server_cmd`` renders the same launch as a ready-made SERVER argument.
DEMO_SERVER_ARGS: tuple[str, ...] = ("-m", "mcpdump.demo")

TOOLS_ONLY_SERVER = ROOT / "tests" / "fixtures" / "tools_only_server.py"
BROKEN_SERVER = ROOT / "tests" / "fixtures" / "broken_server.py"
LINE_ECHO_SERVER = ROOT / "tests" / "fixtures" / "line_echo_server.py"

#: Factories that build a launch command with extra arguments spliced in. Not
#: ``Callable[[str], str]``: both factories take a default, which ``Callable`` cannot express.
ServerCommand = Callable[..., str]


@pytest.fixture(autouse=True)
def _pinned_language() -> Iterator[None]:
    """Pin in-process output to English, the product default and assertion baseline."""
    previous = i18n.current_language()
    i18n.set_language("en")
    yield
    i18n.set_language(previous)


@pytest.fixture(scope="session")
def echo_server_cmd() -> str:
    """Launch command for the example server that declares every capability."""
    return f'"{sys.executable}" {" ".join(DEMO_SERVER_ARGS)}'


@pytest.fixture(scope="session")
def tools_only_server_cmd() -> str:
    """A server that declares only the tools capability, for capability gating."""
    return f'"{sys.executable}" "{TOOLS_ONLY_SERVER}"'


@pytest.fixture(scope="session")
def broken_server() -> ServerCommand:
    """Build the launch command for the "intentionally broken" server by fault name.

    ``broken_server("none")`` is the compliant baseline: the per-check tests rely on
    only the targeted check failing, which proves that check owns that one defect.
    """

    def make(faults: str = "all") -> str:
        return f'"{sys.executable}" "{BROKEN_SERVER}" --faults={faults}'

    return make


@pytest.fixture(scope="session")
def wire_server() -> ServerCommand:
    """Build the minimal peer launch command used by the proxy tests; ``args`` is spliced
    in after the executable verbatim.

    It returns a command line rather than taking an argument list because
    ``split_command``'s quote handling is itself under test.
    """

    def make(args: str = "") -> str:
        suffix = f" {args}" if args else ""
        return f'"{sys.executable}" "{LINE_ECHO_SERVER}"{suffix}'

    return make


@pytest.fixture
def run_check() -> Callable[..., CheckReport]:
    """Run one conformance check in process and return the ``CheckReport``.

    In process so assertions read ``CheckResult`` directly without parsing JSON; the
    subprocess path is covered by ``test_check_cli.py``.
    """

    def _run(server: str, *, timeout: float = 30.0) -> CheckReport:
        opts = SessionOptions(server=server, timeout=timeout, show_server_stderr=False)
        with open_session(opts) as session:
            return run_checks(opts, session)

    return _run


def _deterministic_child_env(env: dict[str, str]) -> None:
    """Pin subprocess output so assertions read the same bytes on every machine.

    CI markers are dropped and typer's styling override is set: ``GITHUB_ACTIONS``
    makes typer render ``--help`` with terminal styling, which rewrites the very
    text several assertions match against, and a child's stdout encoding follows
    the platform locale unless ``PYTHONIOENCODING`` says otherwise.
    """
    env.pop("GITHUB_ACTIONS", None)
    env.pop("FORCE_COLOR", None)
    env.pop("PY_COLORS", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"


def _make_env(language: str) -> dict[str, str]:
    """Build a subprocess environment: put src on PYTHONPATH and pin the output language."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MCPDUMP_LANG"] = language
    _deterministic_child_env(env)
    return env


class FakeHome:
    """A fake user home directory, holding config files in each client's real format.

    Testing discovery against the real home would tie assertions to one developer's
    setup (one person has Cursor installed, another does not); this points ``HOME`` /
    ``USERPROFILE`` / ``APPDATA`` at a clean directory instead.
    """

    def __init__(self, base: pathlib.Path) -> None:
        self.base = base
        self.home = base / "home"
        self.project = base / "project"
        self.home.mkdir(parents=True, exist_ok=True)
        self.project.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        relative: str,
        payload: object,
        *,
        root: str = "home",
    ) -> pathlib.Path:
        """Write a config file. A string ``payload`` goes in verbatim (used to build JSONC)."""
        base = self.project if root == "project" else self.home
        path = base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        text = payload if isinstance(payload, str) else json.dumps(payload, indent=2)
        path.write_text(text, encoding="utf-8")
        return path

    def env(self, **extra: str) -> dict[str, str]:
        """Environment variables pointing at this fake home. ``APPDATA`` has to move too,
        otherwise Windows reads the real Claude Desktop config from the dev machine. The
        child's output is pinned for deterministic bytes, same as ``_make_env``.
        """
        env = {
            **os.environ,
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "APPDATA": str(self.home / "AppData"),
        }
        env.pop("XDG_CONFIG_HOME", None)
        env.pop("COPILOT_HOME", None)
        _deterministic_child_env(env)
        env.update(extra)
        return env


@pytest.fixture
def fake_home(tmp_path: pathlib.Path) -> FakeHome:
    return FakeHome(tmp_path)


@pytest.fixture(scope="session")
def mcpdump_env() -> dict[str, str]:
    """Environment variables for the default (English) output."""
    return _make_env("en")


@pytest.fixture(scope="session")
def mcpdump_env_zh() -> dict[str, str]:
    """Chinese output environment, to prove MCPDUMP_LANG=zh really switches the language."""
    return _make_env("zh")


def run_mcpdump(
    args: list[str], env: dict[str, str], timeout: float = 60.0
) -> subprocess.CompletedProcess[str]:
    """Run mcpdump in a subprocess and return the CompletedProcess."""
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

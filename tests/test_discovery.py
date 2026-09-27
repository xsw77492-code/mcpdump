"""Unit tests for ``services/discovery.py``.

The subject is the boundary of four constraints: paths are never guessed (``platform`` /
``environ`` / ``project_dir`` are injected), secrets never leak, unreadable configs name the
exact entry, and each client's format differs.
"""

from __future__ import annotations

import json
import pathlib
import socket
import sys
from pathlib import Path

import pytest

from conftest import FakeHome
from mcpdump.services.discovery import (
    CLIENTS,
    Availability,
    ClientReport,
    ClientSpec,
    CommandShape,
    ConfigPath,
    Diagnosis,
    Issue,
    ListShape,
    Problem,
    Root,
    ServerEntry,
    TransportKind,
    candidate_paths,
    diagnose,
    load_jsonc,
    referenced_variables,
    resolve_root,
    scan,
    static_issues,
    strip_jsonc,
    tcp_probe,
)

#: A string that "looks like a real secret". Any assertion that finds it in the output fails.
SECRET = "ghp_literalsecret123"


# ---------------------------------------------------------------- builder helpers


def _spec(**overrides: object) -> ClientSpec:
    """A minimal usable client spec: the config sits at ``mcp.json`` under the project root."""
    base: dict[str, object] = {
        "id": "test",
        "name": "Test",
        "paths": (ConfigPath(Root.PROJECT, "mcp.json"),),
    }
    base.update(overrides)
    return ClientSpec(**base)  # type: ignore[arg-type]


def _load(
    tmp_path: Path,
    payload: object,
    **spec_kwargs: object,
) -> ClientReport:
    """Write ``payload`` into the project root's ``mcp.json``, then read it back via ``scan``.

    It goes through ``scan`` rather than ``_read_client`` because path resolution and file
    classification live inside ``scan``; bypassing it would leave them untested.
    """
    (tmp_path / "mcp.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    reports = scan(
        platform="linux",
        environ={"HOME": str(tmp_path)},
        project_dir=tmp_path,
        clients=(_spec(**spec_kwargs),),
    )
    assert len(reports) == 1
    return reports[0]


def _entry(**overrides: object) -> ServerEntry:
    """A stdio entry that **can run** by default: the command is the current interpreter."""
    base: dict[str, object] = {
        "name": "s",
        "client_id": "c",
        "client_name": "C",
        "source": Path("mcp.json"),
        "kind": TransportKind.STDIO,
        "command": (sys.executable,),
    }
    base.update(overrides)
    return ServerEntry(**base)  # type: ignore[arg-type]


def _report(*entries: ServerEntry) -> ClientReport:
    return ClientReport(spec=_spec(), path=Path("mcp.json"), servers=entries)


# ---------------------------------------------------------------- root resolution


class TestResolveRoot:
    def test_home_from_HOME(self, tmp_path: Path) -> None:
        got = resolve_root(
            Root.HOME, platform="linux", environ={"HOME": str(tmp_path)}, project_dir=tmp_path
        )
        assert got == tmp_path

    def test_home_falls_back_to_USERPROFILE(self, tmp_path: Path) -> None:
        """Windows has no ``HOME``, only ``USERPROFILE``."""
        got = resolve_root(
            Root.HOME,
            platform="win32",
            environ={"USERPROFILE": str(tmp_path)},
            project_dir=tmp_path,
        )
        assert got == tmp_path

    def test_home_absent_resolves_to_nothing(self, tmp_path: Path) -> None:
        """Unresolvable is a **normal case**, not an exception — the caller reads it as "absent"."""
        assert resolve_root(Root.HOME, platform="linux", environ={}, project_dir=tmp_path) is None

    def test_appdata_on_windows(self, tmp_path: Path) -> None:
        got = resolve_root(
            Root.APPDATA, platform="win32", environ={"APPDATA": str(tmp_path)}, project_dir=tmp_path
        )
        assert got == tmp_path

    def test_appdata_on_windows_without_the_variable(self, tmp_path: Path) -> None:
        assert (
            resolve_root(Root.APPDATA, platform="win32", environ={}, project_dir=tmp_path) is None
        )

    def test_appdata_never_resolves_off_windows(self, tmp_path: Path) -> None:
        """On Linux a variable named APPDATA is not Windows' %APPDATA%; honouring it scans an
        unrelated directory, so return None.
        """
        got = resolve_root(
            Root.APPDATA,
            platform="linux",
            environ={"APPDATA": str(tmp_path)},
            project_dir=tmp_path,
        )
        assert got is None

    def test_xdg_explicit_wins_everywhere(self, tmp_path: Path) -> None:
        got = resolve_root(
            Root.XDG_CONFIG,
            platform="linux",
            environ={"XDG_CONFIG_HOME": str(tmp_path), "HOME": "/definitely/not/used"},
            project_dir=tmp_path,
        )
        assert got == tmp_path

    def test_xdg_on_windows_falls_back_to_appdata(self, tmp_path: Path) -> None:
        got = resolve_root(
            Root.XDG_CONFIG,
            platform="win32",
            environ={"APPDATA": str(tmp_path)},
            project_dir=tmp_path,
        )
        assert got == tmp_path

    def test_xdg_on_posix_defaults_to_dot_config(self, tmp_path: Path) -> None:
        got = resolve_root(
            Root.XDG_CONFIG,
            platform="darwin",
            environ={"HOME": str(tmp_path)},
            project_dir=tmp_path,
        )
        assert got == tmp_path / ".config"

    def test_macos_app_support_requires_darwin(self, tmp_path: Path) -> None:
        environ = {"HOME": str(tmp_path)}
        assert (
            resolve_root(
                Root.MACOS_APP_SUPPORT, platform="linux", environ=environ, project_dir=tmp_path
            )
            is None
        )
        got = resolve_root(
            Root.MACOS_APP_SUPPORT, platform="darwin", environ=environ, project_dir=tmp_path
        )
        assert got == tmp_path / "Library" / "Application Support"

    def test_copilot_home_explicit_beats_the_default(self, tmp_path: Path) -> None:
        environ = {"COPILOT_HOME": str(tmp_path / "explicit"), "HOME": "/ignored"}
        got = resolve_root(
            Root.COPILOT_HOME, platform="linux", environ=environ, project_dir=tmp_path
        )
        assert got == tmp_path / "explicit"

    def test_copilot_home_defaults_under_home(self, tmp_path: Path) -> None:
        got = resolve_root(
            Root.COPILOT_HOME,
            platform="linux",
            environ={"HOME": str(tmp_path)},
            project_dir=tmp_path,
        )
        assert got == tmp_path / ".copilot"

    def test_project_root_is_passed_through(self, tmp_path: Path) -> None:
        got = resolve_root(
            Root.PROJECT, platform="linux", environ={}, project_dir=tmp_path / "elsewhere"
        )
        assert got == tmp_path / "elsewhere"


class TestCandidatePaths:
    def test_platform_limited_entry_is_skipped_elsewhere(self, tmp_path: Path) -> None:
        spec = _spec(
            paths=(
                ConfigPath(Root.HOME, "mac-only.json", "darwin"),
                ConfigPath(Root.HOME, "anywhere.json"),
            )
        )
        got = candidate_paths(
            spec, platform="win32", environ={"HOME": str(tmp_path)}, project_dir=tmp_path
        )
        assert got == (tmp_path / "anywhere.json",)

    def test_unresolvable_root_is_skipped_not_guessed(self, tmp_path: Path) -> None:
        spec = _spec(
            paths=(
                ConfigPath(Root.APPDATA, "win-only.json", "win32"),
                ConfigPath(Root.HOME, "fallback.json"),
            )
        )
        got = candidate_paths(
            spec, platform="linux", environ={"HOME": str(tmp_path)}, project_dir=tmp_path
        )
        assert got == (tmp_path / "fallback.json",)

    def test_duplicate_paths_collapse(self, tmp_path: Path) -> None:
        """``XDG_CONFIG_HOME`` and ``~/.config`` often coincide on POSIX; do not read twice."""
        spec = _spec(
            paths=(
                ConfigPath(Root.XDG_CONFIG, "zed/settings.json"),
                ConfigPath(Root.HOME, ".config/zed/settings.json"),
            )
        )
        got = candidate_paths(
            spec, platform="linux", environ={"HOME": str(tmp_path)}, project_dir=tmp_path
        )
        assert got == (tmp_path / ".config" / "zed" / "settings.json",)

    def test_order_is_preserved(self, tmp_path: Path) -> None:
        """Order is priority: the first one that exists is selected and must not be shuffled."""
        spec = _spec(
            paths=(
                ConfigPath(Root.HOME, "first.json"),
                ConfigPath(Root.PROJECT, "second.json"),
                ConfigPath(Root.HOME, "third.json"),
            )
        )
        got = candidate_paths(
            spec, platform="linux", environ={"HOME": str(tmp_path)}, project_dir=tmp_path
        )
        assert [p.name for p in got] == ["first.json", "second.json", "third.json"]

    def test_claude_desktop_resolves_differently_per_platform(self, tmp_path: Path) -> None:
        """One spec table, each platform resolving its own paths — "no guessing" made testable."""
        spec = next(s for s in CLIENTS if s.id == "claude-desktop")
        tail = "Claude/claude_desktop_config.json"

        win = candidate_paths(
            spec, platform="win32", environ={"APPDATA": "/a"}, project_dir=tmp_path
        )
        mac = candidate_paths(
            spec, platform="darwin", environ={"HOME": "/h"}, project_dir=tmp_path
        )
        lin = candidate_paths(spec, platform="linux", environ={"HOME": "/h"}, project_dir=tmp_path)

        assert win == (Path("/a") / tail,)
        # macOS also has an XDG fallback: some users really do keep config XDG-style; an
        # extra entry costs one more exists(), while omitting it hides the client forever.
        assert mac == (
            Path("/h") / "Library" / "Application Support" / tail,
            Path("/h") / ".config" / tail,
        )
        assert lin == (Path("/h") / ".config" / tail,)


class TestClientSpecs:
    """Data quality of the spec table. A mistake never errors; it **never finds** the client."""

    def test_ids_are_unique(self) -> None:
        ids = [spec.id for spec in CLIENTS]
        assert len(ids) == len(set(ids))

    def test_every_spec_has_paths_keys_and_a_source(self) -> None:
        for spec in CLIENTS:
            assert spec.paths, spec.id
            assert spec.keys, spec.id
            assert spec.source, spec.id

    def test_path_tails_are_relative(self) -> None:
        """A ``tail`` written as an absolute path is read as "change root", discarding the root."""
        for spec in CLIENTS:
            for item in spec.paths:
                assert not pathlib.PurePosixPath(item.tail).is_absolute(), (spec.id, item.tail)
                assert not pathlib.PureWindowsPath(item.tail).is_absolute(), (spec.id, item.tail)

    def test_platform_restricted_paths_pair_with_the_right_root(self) -> None:
        """``platform`` and ``root`` must match: ``win32`` with ``MACOS_APP_SUPPORT`` resolves to
        nothing on any machine, silently.
        """
        allowed = {
            "win32": {Root.APPDATA},
            "darwin": {Root.MACOS_APP_SUPPORT},
        }
        for spec in CLIENTS:
            for item in spec.paths:
                if item.platform is None:
                    continue
                assert item.platform in allowed, (spec.id, item.platform)
                assert item.root in allowed[item.platform], (spec.id, item.root)


# ---------------------------------------------------------------- JSONC


class TestStripJsonc:
    def test_line_comment(self) -> None:
        assert json.loads(strip_jsonc('{"a": 1} // done')) == {"a": 1}

    def test_block_comment(self) -> None:
        assert json.loads(strip_jsonc('{"a": /* why */ 1}')) == {"a": 1}

    def test_trailing_comma_in_object(self) -> None:
        assert json.loads(strip_jsonc('{"a": 1,}')) == {"a": 1}

    def test_trailing_comma_in_array(self) -> None:
        assert json.loads(strip_jsonc("[1, 2,]")) == [1, 2]

    def test_trailing_comma_followed_by_a_comment(self) -> None:
        assert json.loads(strip_jsonc('{"a": 1, // end\n}')) == {"a": 1}

    def test_double_slash_inside_a_string_is_not_a_comment(self) -> None:
        """A URL contains ``//``; treating it as a comment truncates the config, which is why the
        scanner is hand-written.
        """
        got = json.loads(strip_jsonc('{"url": "https://example.com/mcp"}'))
        assert got["url"] == "https://example.com/mcp"

    def test_block_comment_marker_inside_a_string_is_not_a_comment(self) -> None:
        got = json.loads(strip_jsonc('{"a": "/* not a comment */"}'))
        assert got["a"] == "/* not a comment */"

    def test_escaped_quote_does_not_end_the_string(self) -> None:
        got = json.loads(strip_jsonc(r'{"a": "quote \" // still inside"}'))
        assert got["a"] == 'quote " // still inside'

    def test_comma_before_a_brace_inside_a_string_survives(self) -> None:
        got = json.loads(strip_jsonc('{"a": "x,}"}'))
        assert got["a"] == "x,}"

    def test_unterminated_block_comment_fails_loudly(self) -> None:
        """A failed strip must fail the parse — silently dropping half a config is far worse."""
        with pytest.raises(json.JSONDecodeError):
            json.loads(strip_jsonc('{"a": 1 /* never closed'))


class TestLoadJsonc:
    def test_accepts_a_utf8_bom(self, tmp_path: Path) -> None:
        """JSON saved by Windows Notepad often carries a BOM; ``json.loads`` rejects it."""
        path = tmp_path / "mcp.json"
        path.write_text('{"a": 1}', encoding="utf-8-sig")
        assert load_jsonc(path) == {"a": 1}


# ---------------------------------------------------------------- client formats


class TestClientFormats:
    def test_map_shape_is_the_default(self, tmp_path: Path) -> None:
        report = _load(tmp_path, {"mcpServers": {"fs": {"command": "node"}}})
        assert [s.name for s in report.servers] == ["fs"]
        assert report.issues == ()

    def test_list_shape_reads_continue_style_arrays(self, tmp_path: Path) -> None:
        report = _load(
            tmp_path,
            {"mcpServers": [{"name": "mem", "command": "npx"}]},
            list_shape=ListShape.LIST,
        )
        assert [s.name for s in report.servers] == ["mem"]

    def test_list_shape_rejects_an_object(self, tmp_path: Path) -> None:
        """Mixed shapes do nothing **silently**, so report it rather than call it "no servers"."""
        report = _load(
            tmp_path,
            {"mcpServers": {"mem": {"command": "npx"}}},
            list_shape=ListShape.LIST,
        )
        assert report.servers == ()
        assert [i.problem for i in report.issues] == [Problem.SERVER_LIST_MALFORMED]

    def test_map_shape_rejects_an_array(self, tmp_path: Path) -> None:
        report = _load(tmp_path, {"mcpServers": [{"name": "mem", "command": "npx"}]})
        assert [i.problem for i in report.issues] == [Problem.SERVER_LIST_MALFORMED]

    def test_vscode_workspace_key(self, tmp_path: Path) -> None:
        report = _load(tmp_path, {"servers": {"t": {"command": "node"}}}, keys=(("servers",),))
        assert [s.name for s in report.servers] == ["t"]

    def test_second_keypath_is_tried_when_the_first_is_absent(self, tmp_path: Path) -> None:
        report = _load(
            tmp_path,
            {"mcpServers": {"t": {"command": "node"}}},
            keys=(("servers",), ("mcpServers",)),
        )
        assert [s.name for s in report.servers] == ["t"]

    def test_zed_nested_command_object(self, tmp_path: Path) -> None:
        """Zed tucks command/args/env entirely inside a ``command`` object."""
        report = _load(
            tmp_path,
            {
                "context_servers": {
                    "z": {"command": {"path": "node", "args": ["x.js"], "env": {"K": "v"}}}
                }
            },
            keys=(("context_servers",),),
            command_shape=CommandShape.NESTED,
        )
        entry = report.servers[0]
        assert entry.command == ("node", "x.js")
        assert entry.env == {"K": "v"}

    def test_zed_string_command_is_treated_as_flat(self, tmp_path: Path) -> None:
        """A string means it is someone else's format; treating it as flat beats erroring."""
        report = _load(
            tmp_path,
            {"context_servers": {"z": {"command": "node"}}},
            keys=(("context_servers",),),
            command_shape=CommandShape.NESTED,
        )
        assert report.servers[0].command == ("node",)

    def test_wildcard_scope_records_the_real_key(self, tmp_path: Path) -> None:
        """The real key names walked under ``projects.*.mcpServers`` must be recorded in scope.

        Otherwise the source can only show ``projects.*`` — not which project this is.
        """
        report = _load(
            tmp_path,
            {"projects": {"/home/me/proj": {"mcpServers": {"fs": {"command": "node"}}}}},
            keys=(("mcpServers",), ("projects", "*", "mcpServers")),
        )
        entry = report.servers[0]
        assert entry.scope == ("projects", "/home/me/proj", "mcpServers")
        assert entry.origin == "Test · projects./home/me/proj.mcpServers"

    def test_two_keypaths_can_both_yield_servers(self, tmp_path: Path) -> None:
        report = _load(
            tmp_path,
            {
                "mcpServers": {"a": {"command": "node"}},
                "projects": {"/p": {"mcpServers": {"b": {"command": "node"}}}},
            },
            keys=(("mcpServers",), ("projects", "*", "mcpServers")),
        )
        assert {s.name for s in report.servers} == {"a", "b"}

    def test_unrelated_config_yields_nothing_and_no_issue(self, tmp_path: Path) -> None:
        """The config has none of our keys — that is the norm, not a problem."""
        report = _load(tmp_path, {"editor.fontSize": 14})
        assert report.servers == ()
        assert report.issues == ()


# ---------------------------------------------------------------- entry building


class TestEntryBuilding:
    def test_command_with_args(self, tmp_path: Path) -> None:
        report = _load(tmp_path, {"mcpServers": {"s": {"command": "npx", "args": ["-y", "pkg"]}}})
        assert report.servers[0].command == ("npx", "-y", "pkg")

    def test_command_given_as_an_array(self, tmp_path: Path) -> None:
        report = _load(tmp_path, {"mcpServers": {"s": {"command": ["node", "a.js"]}}})
        assert report.servers[0].command == ("node", "a.js")

    def test_url_only_is_inferred_as_http(self, tmp_path: Path) -> None:
        """No ``type``, only a url: the legacy form — classify as HTTP but **mark it inferred**."""
        report = _load(tmp_path, {"mcpServers": {"s": {"url": "https://x/sse"}}})
        entry = report.servers[0]
        assert entry.kind is TransportKind.HTTP
        assert entry.kind_inferred is True

    def test_declared_type_is_trusted(self, tmp_path: Path) -> None:
        report = _load(tmp_path, {"mcpServers": {"s": {"type": "sse", "url": "https://x/sse"}}})
        entry = report.servers[0]
        assert entry.kind is TransportKind.SSE
        assert entry.kind_inferred is False

    def test_streamable_http_is_an_alias_for_http(self, tmp_path: Path) -> None:
        report = _load(tmp_path, {"mcpServers": {"s": {"type": "streamable-http", "url": "https://x"}}})
        assert report.servers[0].kind is TransportKind.HTTP

    def test_unknown_declared_type_falls_back_to_inference(self, tmp_path: Path) -> None:
        """A declared but unknown ``type``: with a command, infer stdio and mark it inferred."""
        report = _load(tmp_path, {"mcpServers": {"s": {"type": "quantum", "command": "node"}}})
        entry = report.servers[0]
        assert entry.kind is TransportKind.STDIO
        assert entry.kind_inferred is True

    def test_entry_with_neither_command_nor_url(self, tmp_path: Path) -> None:
        report = _load(tmp_path, {"mcpServers": {"s": {"args": ["-y"]}}})
        assert report.servers == ()
        assert [i.problem for i in report.issues] == [Problem.ENTRY_INVALID]
        # detail must identify which config entry — "an entry is broken" says nothing at all.
        assert report.issues[0].detail == "s @ mcpServers"

    def test_entry_that_is_not_an_object(self, tmp_path: Path) -> None:
        report = _load(tmp_path, {"mcpServers": {"s": "npx -y pkg"}})
        assert report.servers == ()
        assert [i.problem for i in report.issues] == [Problem.ENTRY_NOT_A_MAP]

    def test_declared_stdio_without_a_command(self, tmp_path: Path) -> None:
        report = _load(tmp_path, {"mcpServers": {"s": {"type": "stdio"}}})
        assert [i.problem for i in report.issues] == [Problem.ENTRY_INVALID]

    def test_declared_http_without_a_url(self, tmp_path: Path) -> None:
        report = _load(tmp_path, {"mcpServers": {"s": {"type": "http"}}})
        assert [i.problem for i in report.issues] == [Problem.ENTRY_INVALID]

    def test_working_directory_alias(self, tmp_path: Path) -> None:
        config = {"mcpServers": {"s": {"command": "node", "workingDirectory": "/w"}}}
        report = _load(tmp_path, config)
        assert report.servers[0].cwd == "/w"

    def test_non_string_env_values_are_kept_as_json(self, tmp_path: Path) -> None:
        """Numeric env values stay strings, never dropped — dropping them makes variables vanish."""
        report = _load(tmp_path, {"mcpServers": {"s": {"command": "node", "env": {"N": 5}}}})
        assert report.servers[0].env == {"N": "5"}

    def test_a_bad_entry_does_not_hide_the_good_one(self, tmp_path: Path) -> None:
        report = _load(
            tmp_path, {"mcpServers": {"good": {"command": "node"}, "bad": {"args": []}}}
        )
        assert [s.name for s in report.servers] == ["good"]
        assert [i.problem for i in report.issues] == [Problem.ENTRY_INVALID]


# ---------------------------------------------------------------- secret redaction


class TestSecretRedaction:
    def _secretive(self) -> ServerEntry:
        return _entry(
            env={"GITHUB_TOKEN": SECRET, "PLAIN": "ok"},
            headers={"Authorization": f"Bearer {SECRET}"},
        )

    def test_entry_dict_lists_names_never_values(self) -> None:
        payload = self._secretive().to_dict()
        assert payload["envKeys"] == ["GITHUB_TOKEN", "PLAIN"]
        assert payload["headerKeys"] == ["Authorization"]
        assert SECRET not in json.dumps(payload)

    def test_diagnosis_dict_is_clean_too(self) -> None:
        """``Diagnosis.to_dict()`` flattens the entry's fields — do not leak them here."""
        payload = Diagnosis(self._secretive()).to_dict()
        assert SECRET not in json.dumps(payload)

    def test_values_survive_scan_but_never_reach_the_payload(self, tmp_path: Path) -> None:
        """Real config to ``to_dict``: values stay internal (launch needs them), none leave."""
        report = _load(
            tmp_path,
            {
                "mcpServers": {
                    "s": {
                        "command": "npx",
                        "env": {"TOKEN": SECRET},
                        "headers": {"Authorization": f"Bearer {SECRET}"},
                    }
                }
            },
        )
        assert report.servers[0].env["TOKEN"] == SECRET
        assert SECRET not in json.dumps(report.to_dict())

    def test_launch_command_is_built_from_the_command_only(self) -> None:
        """The launch command must contain only the command; env values must never mix in."""
        assert SECRET not in self._secretive().launch_command()


# ---------------------------------------------------------------- static diagnostics


class TestStaticIssues:
    def test_a_runnable_command_has_no_issues(self) -> None:
        assert static_issues(_entry(), environ={}) == ()

    def test_missing_command(self) -> None:
        issues = static_issues(_entry(command=("definitely-not-a-real-binary-xyz",)), environ={})
        assert [i.problem for i in issues] == [Problem.COMMAND_MISSING]
        assert issues[0].detail == "definitely-not-a-real-binary-xyz"

    def test_absolute_path_to_an_existing_file_counts(self, tmp_path: Path) -> None:
        """The command is not on PATH but an absolute path was given — that can still run."""
        script = tmp_path / "server-binary"
        script.write_text("", encoding="utf-8")
        assert static_issues(_entry(command=(str(script),)), environ={}) == ()

    def test_referenced_variable_that_is_not_set(self) -> None:
        issues = static_issues(_entry(env={"TOKEN": "${MCPDUMP_TEST_UNSET}"}), environ={})
        assert [i.problem for i in issues] == [Problem.ENV_MISSING]
        assert issues[0].detail == "MCPDUMP_TEST_UNSET"

    def test_referenced_variable_that_is_set(self) -> None:
        entry = _entry(env={"TOKEN": "${MCPDUMP_TEST_SET}"})
        assert static_issues(entry, environ={"MCPDUMP_TEST_SET": "x"}) == ()

    def test_a_literal_value_is_not_a_missing_variable(self) -> None:
        """A literal secret is not a variable reference; calling it "unset" is a false positive."""
        assert static_issues(_entry(env={"TOKEN": SECRET}), environ={}) == ()

    def test_missing_working_directory(self, tmp_path: Path) -> None:
        issues = static_issues(_entry(cwd=str(tmp_path / "no-such-dir")), environ={})
        assert [i.problem for i in issues] == [Problem.CWD_MISSING]

    def test_non_stdio_short_circuits(self) -> None:
        """An HTTP entry cannot run in mcpdump; skip the irrelevant "command missing" noise."""
        entry = _entry(kind=TransportKind.SSE, command=(), url="https://x/sse")
        issues = static_issues(entry, environ={})
        assert [i.problem for i in issues] == [Problem.TRANSPORT_UNSUPPORTED]
        assert issues[0].detail == "sse"

    def test_everything_wrong_at_once(self, tmp_path: Path) -> None:
        """The three problems are independent; reporting the first must not swallow the others."""
        entry = _entry(
            command=("nope-xyz",),
            env={"T": "${MCPDUMP_TEST_UNSET}"},
            cwd=str(tmp_path / "no-such-dir"),
        )
        issues = static_issues(entry, environ={})
        assert {i.problem for i in issues} == {
            Problem.COMMAND_MISSING,
            Problem.ENV_MISSING,
            Problem.CWD_MISSING,
        }


class TestReferencedVariables:
    def test_finds_sorts_and_dedupes(self) -> None:
        entry = _entry(env={"A": "${B}", "C": "${A}"}, headers={"H": "Bearer ${B}"})
        assert referenced_variables(entry) == ("A", "B")

    def test_ignores_other_placeholder_syntax(self) -> None:
        """VS Code's ``${input:xxx}`` is a separate mechanism (prompted input), not an env var."""
        entry = _entry(env={"A": "${input:token}", "B": "$NOT_BRACED"})
        assert referenced_variables(entry) == ()

    def test_no_variables_at_all(self) -> None:
        assert referenced_variables(_entry(env={"A": "plain"})) == ()


class TestTcpProbe:
    def test_no_url_means_nothing_to_probe(self) -> None:
        assert tcp_probe(_entry(url=None)) == ()

    def test_url_without_a_hostname(self) -> None:
        assert tcp_probe(_entry(kind=TransportKind.HTTP, command=(), url="file:///x")) == ()

    def test_a_listening_port_is_reachable(self) -> None:
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            entry = _entry(
                kind=TransportKind.HTTP, command=(), url=f"http://127.0.0.1:{port}/sse"
            )
            assert tcp_probe(entry, timeout=2.0) == ()

    def test_a_port_nobody_listens_on(self) -> None:
        """Be specific rather than a vague "cannot connect".

        ``PORT_TIMEOUT`` also passes: some environments drop the SYN with a firewall instead of
        replying RST, which still means "cannot connect".
        """
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        entry = _entry(kind=TransportKind.HTTP, command=(), url=f"http://127.0.0.1:{port}/sse")
        issues = tcp_probe(entry, timeout=1.0)
        assert issues
        assert issues[0].problem in {Problem.PORT_NOT_LISTENING, Problem.PORT_TIMEOUT}


class TestDiagnose:
    def test_static_pass_yields_ready(self) -> None:
        """Finding no issue ≠ actually connected. This tier can only be called ready."""
        diags = diagnose([_report(_entry())], environ={})
        assert diags[0].availability is Availability.READY
        assert diags[0].usable is True
        assert diags[0].probed is False

    def test_issues_yield_blocked(self) -> None:
        diags = diagnose([_report(_entry(command=("nope-xyz",)))], environ={})
        assert diags[0].availability is Availability.BLOCKED
        assert diags[0].usable is False

    def test_probe_runs_only_where_static_checks_passed(self) -> None:
        """A stdio entry that fails static checks is not probed: a real connect would only mask
        the true cause (the user sees "timeout" when the command does not exist).
        """
        probed: list[str] = []

        def probe(entry: ServerEntry) -> list[Issue]:
            probed.append(entry.name)
            return [Issue(Problem.PROBE_FAILED, "boom")]

        diags = diagnose(
            [_report(_entry(name="good"), _entry(name="bad", command=("nope-xyz",)))],
            probe=probe,
            environ={},
        )
        assert probed == ["good"]
        assert [d.probed for d in diags] == [True, False]

    def test_a_successful_probe_upgrades_to_verified(self) -> None:
        diags = diagnose([_report(_entry())], probe=lambda entry: [], environ={})
        assert diags[0].availability is Availability.VERIFIED
        assert diags[0].usable is True

    def test_a_failed_probe_makes_a_ready_entry_blocked(self) -> None:
        diags = diagnose(
            [_report(_entry())],
            probe=lambda entry: [Issue(Problem.PROBE_FAILED, "x")],
            environ={},
        )
        assert diags[0].availability is Availability.BLOCKED

    def test_url_entries_are_probed_even_though_static_fails(self) -> None:
        """A URL entry's verdict is always "unsupported", but the port probe is still useful."""
        probed: list[str] = []

        def probe(entry: ServerEntry) -> list[Issue]:
            probed.append(entry.name)
            return []

        entry = _entry(name="web", kind=TransportKind.HTTP, command=(), url="https://x/sse")
        diags = diagnose([_report(entry)], probe=probe, environ={})
        assert probed == ["web"]
        assert [i.problem for i in diags[0].issues] == [Problem.TRANSPORT_UNSUPPORTED]

    def test_no_probe_leaves_everything_unprobed(self) -> None:
        diags = diagnose([_report(_entry(), _entry(name="other"))], environ={})
        assert all(not d.probed for d in diags)


# ---------------------------------------------------------------- scanning


def _by_id(reports: tuple[ClientReport, ...], client_id: str) -> ClientReport:
    return next(r for r in reports if r.spec.id == client_id)


class TestScan:
    def test_finds_configs_under_a_fake_home(self, fake_home: FakeHome) -> None:
        fake_home.write(".cursor/mcp.json", {"mcpServers": {"fs": {"command": "node"}}})

        reports = scan(
            platform="linux", environ=fake_home.env(), project_dir=fake_home.project
        )

        cursor = _by_id(reports, "cursor")
        assert cursor.found
        assert [s.name for s in cursor.servers] == ["fs"]

    def test_a_client_that_is_not_installed_is_not_an_error(self, fake_home: FakeHome) -> None:
        """Cursor not installed is not an "error", only "it does not exist on this machine"."""
        reports = scan(platform="linux", environ=fake_home.env(), project_dir=fake_home.project)

        assert all(not r.found for r in reports)
        assert all(r.issues == () for r in reports)

    def test_only_the_requested_clients_are_scanned(self, fake_home: FakeHome) -> None:
        fake_home.write(".cursor/mcp.json", {"mcpServers": {"fs": {"command": "node"}}})
        spec = next(s for s in CLIENTS if s.id == "cursor")

        reports = scan(
            platform="linux",
            environ=fake_home.env(),
            project_dir=fake_home.project,
            clients=(spec,),
        )

        assert [r.spec.id for r in reports] == ["cursor"]

    def test_the_first_existing_candidate_wins(self, fake_home: FakeHome) -> None:
        """Cursor has two candidates, global and workspace; order is priority."""
        fake_home.write(".cursor/mcp.json", {"mcpServers": {"global": {"command": "node"}}})
        fake_home.write(
            ".cursor/mcp.json",
            {"mcpServers": {"workspace": {"command": "node"}}},
            root="project",
        )

        reports = scan(platform="linux", environ=fake_home.env(), project_dir=fake_home.project)

        cursor = _by_id(reports, "cursor")
        assert [s.name for s in cursor.servers] == ["global"]
        assert cursor.path == fake_home.home / ".cursor" / "mcp.json"

    def test_workspace_configs_follow_the_injected_project_dir(self, fake_home: FakeHome) -> None:
        """Workspace configs follow ``project_dir`` — which may not be the cwd."""
        fake_home.write(
            ".vscode/mcp.json", {"servers": {"t": {"command": "node"}}}, root="project"
        )

        reports = scan(platform="linux", environ=fake_home.env(), project_dir=fake_home.project)

        vscode = _by_id(reports, "vscode")
        assert vscode.found
        assert [s.name for s in vscode.servers] == ["t"]

    def test_jsonc_config_is_read_not_rejected(self, fake_home: FakeHome) -> None:
        """VS Code's ``mcp.json`` allows comments and trailing commas; calling it "broken" is a
        false positive.
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

        reports = scan(platform="linux", environ=fake_home.env(), project_dir=fake_home.project)

        cursor = _by_id(reports, "cursor")
        assert [s.name for s in cursor.servers] == ["fs"]
        assert cursor.issues == ()

    def test_broken_json_is_reported_against_the_file(self, fake_home: FakeHome) -> None:
        fake_home.write(".cursor/mcp.json", '{"mcpServers": ')

        reports = scan(platform="linux", environ=fake_home.env(), project_dir=fake_home.project)

        cursor = _by_id(reports, "cursor")
        assert cursor.found
        assert [i.problem for i in cursor.issues] == [Problem.CONFIG_INVALID]
        assert cursor.servers == ()

    def test_a_top_level_array_is_reported(self, fake_home: FakeHome) -> None:
        fake_home.write(".cursor/mcp.json", "[1, 2]")

        reports = scan(platform="linux", environ=fake_home.env(), project_dir=fake_home.project)

        assert [i.problem for i in _by_id(reports, "cursor").issues] == [Problem.CONFIG_NOT_A_MAP]

    def test_a_bad_entry_does_not_hide_the_good_one(self, fake_home: FakeHome) -> None:
        fake_home.write(
            ".cursor/mcp.json",
            {"mcpServers": {"good": {"command": "node"}, "bad": {"args": []}}},
        )

        reports = scan(platform="linux", environ=fake_home.env(), project_dir=fake_home.project)

        cursor = _by_id(reports, "cursor")
        assert [s.name for s in cursor.servers] == ["good"]
        assert [i.problem for i in cursor.issues] == [Problem.ENTRY_INVALID]

    def test_reports_are_ordered_like_the_spec_table(self, fake_home: FakeHome) -> None:
        """Report order = spec table order. Rendering relies on it; if shuffled, the list jumps."""
        fake_home.write(".cursor/mcp.json", {"mcpServers": {"fs": {"command": "node"}}})
        fake_home.write(".continue/config.json", {"mcpServers": []})

        reports = scan(platform="linux", environ=fake_home.env(), project_dir=fake_home.project)

        expected = [s.id for s in CLIENTS if s.id in {"cursor", "continue"}]
        assert [r.spec.id for r in reports] == expected

"""Find MCP servers the user has already configured in other AI clients.

The user has already written the launch command once, in Claude Desktop or
Cursor; discovering it beats asking them to type it again.

Four constraints: no guessing paths or formats (every location carries a
``source``, and platform and environment are injectable so all three platforms
are testable anywhere); secrets never leave (only variable names are exposed,
never values, in no return value including ``to_dict()``); no I/O beyond reading
by default (``--probe`` opts in); and nothing is skipped silently, so broken JSON
or a moved path is reported rather than read as "I never set that server up".

Client names (Claude Desktop / Cursor ...) are proper nouns and stay identical in
every language, so they are not in the message catalog.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import sys
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

__all__ = [
    "CLIENTS",
    "Availability",
    "ClientReport",
    "ClientSpec",
    "ConfigPath",
    "Diagnosis",
    "Issue",
    "Problem",
    "Root",
    "ServerEntry",
    "TransportKind",
    "candidate_paths",
    "diagnose",
    "load_jsonc",
    "referenced_variables",
    "scan",
    "static_issues",
    "strip_jsonc",
    "tcp_probe",
]


# ---------------------------------------------------------------- path resolution


class Root(str, Enum):
    """Base directories a config path can be anchored to.

    ``APPDATA`` does not exist off Windows (returns ``None``) and ``XDG_CONFIG``
    falls back to ``%APPDATA%`` on Windows.
    """

    HOME = "home"
    APPDATA = "appdata"
    XDG_CONFIG = "xdg_config"
    MACOS_APP_SUPPORT = "macos_app_support"
    COPILOT_HOME = "copilot_home"
    PROJECT = "project"


@dataclass(frozen=True)
class ConfigPath:
    """One candidate config location. ``platform`` of ``None`` means all three."""

    root: Root
    tail: str
    platform: str | None = None


def resolve_root(
    root: Root,
    *,
    platform: str,
    environ: Mapping[str, str],
    project_dir: Path,
) -> Path | None:
    """Resolve a base directory to an absolute path, or ``None`` if unresolvable.

    ``None`` is a normal outcome: ``%APPDATA%`` simply does not exist on Linux,
    which means this client cannot exist on this machine either.
    """
    home = environ.get("HOME") or environ.get("USERPROFILE")
    if root is Root.PROJECT:
        return project_dir
    if root is Root.HOME:
        return Path(home) if home else None
    if root is Root.APPDATA:
        if platform == "win32":
            appdata = environ.get("APPDATA")
            return Path(appdata) if appdata else None
        # Such paths are always platform-qualified, so reaching here means the
        # spec table is missing a platform. Return None rather than invent one.
        return None
    if root is Root.XDG_CONFIG:
        xdg = environ.get("XDG_CONFIG_HOME")
        if xdg:
            return Path(xdg)
        if platform == "win32":
            appdata = environ.get("APPDATA")
            return Path(appdata) if appdata else None
        return Path(home) / ".config" if home else None
    if root is Root.MACOS_APP_SUPPORT:
        if platform != "darwin" or not home:
            return None
        return Path(home) / "Library" / "Application Support"
    if root is Root.COPILOT_HOME:
        explicit = environ.get("COPILOT_HOME")
        if explicit:
            return Path(explicit)
        return Path(home) / ".copilot" if home else None
    return None


def candidate_paths(
    spec: ClientSpec,
    *,
    platform: str,
    environ: Mapping[str, str],
    project_dir: Path,
) -> tuple[Path, ...]:
    """All candidate paths for one client on this platform, in priority order."""
    out: list[Path] = []
    for item in spec.paths:
        if item.platform is not None and item.platform != platform:
            continue
        base = resolve_root(
            item.root, platform=platform, environ=environ, project_dir=project_dir
        )
        if base is None:
            continue
        path = base / item.tail
        if path not in out:
            out.append(path)
    return tuple(out)


# ---------------------------------------------------------------- client specs


class TransportKind(str, Enum):
    """The transport a config declares. Unrelated to what mcpdump supports."""

    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"
    WS = "ws"


class ListShape(str, Enum):
    """Container shape of the server list. Almost all are ``MAP``; Continue is ``LIST``."""

    MAP = "map"
    LIST = "list"


class CommandShape(str, Enum):
    """Field shape of the launch command. Zed nests it inside a ``command`` object."""

    FLAT = "flat"
    NESTED = "nested"


@dataclass(frozen=True)
class ClientSpec:
    """Where a client keeps its config and how to read it.

    ``keys`` holds several key paths, all tried in order, since one client may
    put servers in different places per scope. ``"*"`` walks every value at that
    level and records the real key names, so a source renders as
    ``projects./home/me/proj`` rather than ``projects.*``.

    ``paths`` are candidates, not one answer: clients have moved their config
    between versions, and a missed candidate is a client that can never be found.
    """

    id: str
    name: str
    paths: tuple[ConfigPath, ...]
    keys: tuple[tuple[str, ...], ...] = (("mcpServers",),)
    list_shape: ListShape = ListShape.MAP
    command_shape: CommandShape = CommandShape.FLAT
    #: Basis for the path (goes into docs) plus a reliability note.
    source: str = ""
    uncertain: bool = False


#: Paths follow the VS Code "discovery sources" table
#: (https://code.visualstudio.com/docs/agents/reference/mcp-configuration);
#: the rest come from each client's own docs, cited per entry in ``source``.
CLIENTS: tuple[ClientSpec, ...] = (
    ClientSpec(
        id="claude-desktop",
        name="Claude Desktop",
        paths=(
            ConfigPath(Root.APPDATA, "Claude/claude_desktop_config.json", "win32"),
            ConfigPath(Root.MACOS_APP_SUPPORT, "Claude/claude_desktop_config.json", "darwin"),
            ConfigPath(Root.XDG_CONFIG, "Claude/claude_desktop_config.json"),
        ),
        source="MCP docs + the VS Code discovery-sources table",
    ),
    ClientSpec(
        id="cursor",
        name="Cursor",
        paths=(
            ConfigPath(Root.HOME, ".cursor/mcp.json"),
            ConfigPath(Root.PROJECT, ".cursor/mcp.json"),
        ),
        source="VS Code discovery-sources table (global + workspace)",
    ),
    ClientSpec(
        id="claude-code",
        name="Claude Code",
        paths=(
            ConfigPath(Root.HOME, ".claude.json"),
            ConfigPath(Root.PROJECT, ".mcp.json"),
        ),
        # user / local scope share ~/.claude.json, but local groups by project path.
        keys=(("mcpServers",), ("projects", "*", "mcpServers")),
        source="Claude Code docs: user/local scope in ~/.claude.json, project scope in .mcp.json",
    ),
    ClientSpec(
        id="vscode",
        name="VS Code",
        paths=(
            ConfigPath(Root.PROJECT, ".vscode/mcp.json"),
            ConfigPath(Root.PROJECT, ".mcp.json"),
            ConfigPath(Root.COPILOT_HOME, "mcp-config.json"),
        ),
        # Workspace format uses servers, portable format uses mcpServers; no mixing.
        keys=(("servers",), ("mcpServers",)),
        source="VS Code docs + GitHub Copilot CLI (<COPILOT_HOME>/mcp-config.json)",
    ),
    ClientSpec(
        id="windsurf",
        name="Windsurf",
        paths=(ConfigPath(Root.HOME, ".codeium/windsurf/mcp_config.json"),),
        source="VS Code discovery-sources table",
    ),
    ClientSpec(
        id="cline",
        name="Cline",
        paths=(
            ConfigPath(
                Root.APPDATA,
                "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
                "win32",
            ),
            ConfigPath(
                Root.XDG_CONFIG,
                "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
            ),
            ConfigPath(
                Root.MACOS_APP_SUPPORT,
                "Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
                "darwin",
            ),
        ),
        source="Cline community docs; unofficial, may move between extension versions",
        uncertain=True,
    ),
    ClientSpec(
        id="continue",
        name="Continue",
        paths=(
            ConfigPath(Root.HOME, ".continue/config.json"),
            ConfigPath(Root.PROJECT, ".continue/config.json"),
        ),
        # The only client using an array; an object there fails silently.
        list_shape=ListShape.LIST,
        source="Continue community docs: mcpServers is an ARRAY, not an object",
        uncertain=True,
    ),
    ClientSpec(
        id="zed",
        name="Zed",
        paths=(
            ConfigPath(Root.XDG_CONFIG, "zed/settings.json"),
            ConfigPath(Root.HOME, ".config/zed/settings.json"),
        ),
        keys=(("context_servers",),),
        # Zed keeps command/args/env inside a command object and supports stdio only.
        command_shape=CommandShape.NESTED,
        source="Zed community docs: key is context_servers, command nested inside it",
        uncertain=True,
    ),
    ClientSpec(
        id="openclaw",
        name="OpenClaw",
        # Four public claims disagree, so all are listed: nonexistent paths get
        # skipped, whereas a single hardcoded guess may never find anything.
        paths=(
            ConfigPath(Root.HOME, ".openclaw/openclaw.json"),
            ConfigPath(Root.HOME, ".openclaw/mcp-servers.json"),
            ConfigPath(Root.HOME, ".openclaw/.mcp.json"),
            ConfigPath(Root.XDG_CONFIG, "openclaw/mcp.json"),
        ),
        keys=(("mcpServers",), ("mcp", "servers")),
        source="Public sources disagree (4 paths, 2 key layouts); all listed as candidates",
        uncertain=True,
    ),
)


# ---------------------------------------------------------------- data model


@dataclass(frozen=True)
class ServerEntry:
    """One server declared in a config.

    ``env`` and ``headers`` hold real secrets and are for internal use only
    (diagnosis, launching); they appear in no ``to_dict()``, log, or render.
    """

    name: str
    client_id: str
    client_name: str
    source: Path
    kind: TransportKind
    scope: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    url: str | None = None
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    #: The config declared no ``type`` and the transport was inferred -- the user
    #: has to be told.
    kind_inferred: bool = False

    @property
    def key(self) -> str:
        """``client:name``. Separates same-named servers across clients."""
        return f"{self.client_id}:{self.name}"

    @property
    def origin(self) -> str:
        """Human-readable source: ``Claude Desktop · mcpServers``."""
        where = ".".join(self.scope) if self.scope else "mcpServers"
        return f"{self.client_name} · {where}"

    def launch_command(self) -> str:
        """Rebuild a launch command mcpdump can take directly.

        Arguments with spaces or backslashes need quoting, or the echoed command
        breaks on paste -- and pasting it is the whole point.
        """
        return " ".join(_quote(part) for part in self.command)

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form. Never includes env / headers values, only names."""
        payload: dict[str, Any] = {
            "name": self.name,
            "client": self.client_id,
            "clientName": self.client_name,
            "origin": self.origin,
            "source": str(self.source),
            "transport": self.kind.value,
            "transportInferred": self.kind_inferred,
            "envKeys": sorted(self.env),
            "headerKeys": sorted(self.headers),
        }
        if self.command:
            payload["command"] = list(self.command)
        if self.url:
            payload["url"] = self.url
        if self.cwd:
            payload["cwd"] = self.cwd
        return payload


def _quote(part: str) -> str:
    """Quote an argument the way the current platform expects.

    Windows needs double quotes (``'`` is not a quote character in cmd); other
    platforms use single quotes.
    """
    if not part:
        return '""'
    if not any(ch in part for ch in " \t\"'\\"):
        return part
    if os.name == "nt":
        return '"' + part.replace('"', '\\"') + '"'
    return "'" + part.replace("'", "'\\''") + "'"


class Problem(str, Enum):
    """A specific, nameable problem. No catch-all "unavailable"."""

    CONFIG_UNREADABLE = "config-unreadable"
    CONFIG_INVALID = "config-invalid"
    CONFIG_NOT_A_MAP = "config-not-a-map"
    SERVER_LIST_MALFORMED = "server-list-malformed"
    ENTRY_INVALID = "entry-invalid"
    ENTRY_NOT_A_MAP = "entry-not-a-map"
    COMMAND_MISSING = "command-missing"
    ENV_MISSING = "env-missing"
    CWD_MISSING = "cwd-missing"
    TRANSPORT_UNSUPPORTED = "transport-unsupported"
    PORT_CLOSED = "port-closed"
    PORT_TIMEOUT = "port-timeout"
    PORT_NOT_LISTENING = "port-not-listening"
    PROBE_FAILED = "probe-failed"


@dataclass(frozen=True)
class Issue:
    """One problem. ``detail`` is the part that makes it actionable (which
    variable, which command)."""

    problem: Problem
    detail: str | None = None


class Availability(str, Enum):
    """How usable a server is.

    Three levels rather than two because "found no problem" and "actually
    connected" differ: reporting an unprobed server as available vouches for
    someone else's server.
    """

    #: Static checks passed, never actually connected.
    READY = "ready"
    #: Connected for real (``--probe``).
    VERIFIED = "verified"
    #: Specific problems, each named.
    BLOCKED = "blocked"


@dataclass(frozen=True)
class Diagnosis:
    """The availability verdict for one server."""

    entry: ServerEntry
    issues: tuple[Issue, ...] = ()
    probed: bool = False

    @property
    def availability(self) -> Availability:
        if self.issues:
            return Availability.BLOCKED
        return Availability.VERIFIED if self.probed else Availability.READY

    @property
    def usable(self) -> bool:
        """Whether mcpdump can connect right now: static checks passed, not probed away."""
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        payload = self.entry.to_dict()
        payload["availability"] = self.availability.value
        payload["probed"] = self.probed
        payload["issues"] = [
            {"problem": issue.problem.value, "detail": issue.detail} for issue in self.issues
        ]
        return payload


@dataclass(frozen=True)
class ClientReport:
    """Scan result for one client.

    ``path`` of ``None`` means it is not on this machine -- the norm, not an
    error. ``issues`` merges file-level and entry-level problems; entry-level
    problems are never dropped silently, or a user reads "I never configured
    this server" when the truth is "configured wrong".
    """

    spec: ClientSpec
    path: Path | None = None
    servers: tuple[ServerEntry, ...] = ()
    issues: tuple[Issue, ...] = ()

    @property
    def found(self) -> bool:
        return self.path is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "client": self.spec.id,
            "clientName": self.spec.name,
            "path": str(self.path) if self.path else None,
            "found": self.found,
            "serverCount": len(self.servers),
            "pathSource": self.spec.source,
            "pathUncertain": self.spec.uncertain,
            "issues": [
                {"problem": issue.problem.value, "detail": issue.detail}
                for issue in self.issues
            ],
        }


# ---------------------------------------------------------------- config parsing

#: JSONC comments. VS Code's ``mcp.json`` allows them and OpenClaw's examples
#: contain ``//``, so ``json.loads`` alone would call such files corrupt.
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _skip_trivia(text: str, index: int) -> int:
    """Skip whitespace and comments from ``index``; return the first real char.

    Extracted because two places need it: the main loop skips comments, and the
    trailing-comma check must cross comments to see the following ``}``/``]``.
    """
    size = len(text)
    while index < size:
        char = text[index]
        if char in " \t\r\n":
            index += 1
        elif char == "/" and index + 1 < size and text[index + 1] == "/":
            while index < size and text[index] not in "\r\n":
                index += 1
        elif char == "/" and index + 1 < size and text[index + 1] == "*":
            index += 2
            while index + 1 < size and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2
        else:
            break
    return index


def strip_jsonc(text: str) -> str:
    """Strip JSONC comments and trailing commas to get strict JSON.

    Hand-written scanning rather than a regex: a regex cannot tell ``//`` inside
    a string from a comment, and configs contain ``"url": "https://..."``.
    """
    out: list[str] = []
    index, size = 0, len(text)
    in_string = False
    while index < size:
        char = text[index]

        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < size:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < size and text[index + 1] in "/*":
            index = _skip_trivia(text, index)
            continue
        if char == ",":
            # Trailing comma: if the next real character after whitespace and
            # comments is } or ], drop it. Crossing comments is required, since
            # ``{"a": 1, // note`` followed by ``}`` is common.
            probe = _skip_trivia(text, index + 1)
            if probe < size and text[probe] in "}]":
                index += 1
                continue
        out.append(char)
        index += 1
    return "".join(out)


def load_jsonc(path: Path) -> Any:
    """Read a file that may be JSONC."""
    return json.loads(strip_jsonc(path.read_text(encoding="utf-8-sig")))


def _walk_keys(
    payload: Any, keys: Sequence[str], scope: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Walk a key path, yielding ``(path actually walked, server list)``.

    ``"*"`` walks every value at the level and records the real key names, so a
    rendered source shows ``projects./home/me/proj`` rather than ``projects.*``.
    """
    if not keys:
        yield scope, payload
        return
    head, rest = keys[0], keys[1:]
    if not isinstance(payload, Mapping):
        return
    if head == "*":
        for name, value in payload.items():
            yield from _walk_keys(value, rest, (*scope, str(name)))
    elif head in payload:
        yield from _walk_keys(payload[head], rest, (*scope, head))


def _iter_entries(listing: Any, shape: ListShape) -> Iterator[tuple[str, Any]]:
    """Normalize a server list into ``(name, entry)`` pairs."""
    if shape is ListShape.LIST:
        if not isinstance(listing, Sequence) or isinstance(listing, (str, bytes)):
            return
        for item in listing:
            if isinstance(item, Mapping) and item.get("name"):
                yield str(item["name"]), item
        return
    if not isinstance(listing, Mapping):
        return
    for name, item in listing.items():
        yield str(name), item


_TYPE_MAP = {
    "stdio": TransportKind.STDIO,
    "http": TransportKind.HTTP,
    "streamable-http": TransportKind.HTTP,
    "sse": TransportKind.SSE,
    "ws": TransportKind.WS,
    "websocket": TransportKind.WS,
}


def _as_str_map(value: Any) -> dict[str, str]:
    """Collect env / headers as ``str -> str``. Non-strings are stringified, not dropped."""
    if not isinstance(value, Mapping):
        return {}
    return {str(k): v if isinstance(v, str) else json.dumps(v) for k, v in value.items()}


def _build_entry(
    spec: ClientSpec,
    path: Path,
    scope: tuple[str, ...],
    name: str,
    raw: Any,
) -> tuple[ServerEntry | None, Issue | None]:
    """Turn one config entry into a ``ServerEntry``, or give the specific reason."""
    if not isinstance(raw, Mapping):
        return None, Issue(Problem.ENTRY_NOT_A_MAP, name)

    if spec.command_shape is CommandShape.NESTED:
        nested = raw.get("command")
        # Zed writes command as an object; a string means another client's format,
        # which flattens more reliably.
        if isinstance(nested, Mapping):
            source: Mapping[str, Any] = {**raw, **nested, "command": nested.get("path")}
        else:
            source = raw
    else:
        source = raw

    env = _as_str_map(source.get("env"))
    headers = _as_str_map(source.get("headers"))
    cwd = source.get("cwd") or source.get("workingDirectory")
    url = source.get("url") if isinstance(source.get("url"), str) else None
    raw_command = source.get("command")

    command: tuple[str, ...] = ()
    if isinstance(raw_command, str) and raw_command:
        command = (raw_command,)
    elif isinstance(raw_command, Sequence) and not isinstance(raw_command, (str, bytes)):
        command = tuple(str(part) for part in raw_command)
    args = source.get("args")
    if command and isinstance(args, Sequence) and not isinstance(args, (str, bytes)):
        command = (*command, *(str(part) for part in args))

    declared = raw.get("type")
    inferred = False
    if isinstance(declared, str) and declared.lower() in _TYPE_MAP:
        kind = _TYPE_MAP[declared.lower()]
    elif command:
        kind = TransportKind.STDIO
        inferred = declared is not None
    elif url:
        # A url with no declared type: the older Claude Desktop / Cursor form.
        # Counted as HTTP but flagged as inferred, so diagnosis says so.
        kind = TransportKind.HTTP
        inferred = True
    else:
        return None, Issue(Problem.ENTRY_INVALID, name)

    if kind is TransportKind.STDIO and not command:
        return None, Issue(Problem.ENTRY_INVALID, name)
    if kind is not TransportKind.STDIO and not url:
        return None, Issue(Problem.ENTRY_INVALID, name)

    return (
        ServerEntry(
            name=name,
            client_id=spec.id,
            client_name=spec.name,
            source=path,
            kind=kind,
            scope=scope,
            command=command,
            url=url,
            cwd=str(cwd) if cwd else None,
            env=env,
            headers=headers,
            kind_inferred=inferred,
        ),
        None,
    )


def _shape_ok(listing: Any, shape: ListShape) -> bool:
    """Whether the container shape matches. Continue uses an array, the rest an
    object; mixing them fails silently.
    """
    if shape is ListShape.LIST:
        return isinstance(listing, Sequence) and not isinstance(listing, (str, bytes))
    return isinstance(listing, Mapping)


def _read_client(spec: ClientSpec, path: Path) -> ClientReport:
    """Read one config file. A missing file is not a problem -- the client is not installed."""
    try:
        payload = load_jsonc(path)
    except OSError as exc:
        return ClientReport(spec, path, issues=(Issue(Problem.CONFIG_UNREADABLE, str(exc)),))
    except (json.JSONDecodeError, ValueError) as exc:
        return ClientReport(spec, path, issues=(Issue(Problem.CONFIG_INVALID, str(exc)),))

    if not isinstance(payload, Mapping):
        return ClientReport(spec, path, issues=(Issue(Problem.CONFIG_NOT_A_MAP),))

    servers: list[ServerEntry] = []
    issues: list[Issue] = []
    for keypath in spec.keys:
        for scope, listing in _walk_keys(payload, keypath):
            where = ".".join(scope) if scope else ".".join(keypath)
            if not _shape_ok(listing, spec.list_shape):
                issues.append(Issue(Problem.SERVER_LIST_MALFORMED, where))
                continue
            for name, raw in _iter_entries(listing, spec.list_shape):
                entry, issue = _build_entry(spec, path, scope, name, raw)
                if entry is not None:
                    servers.append(entry)
                elif issue is not None:
                    # Name goes into detail so the user can find the entry in the file.
                    issues.append(Issue(issue.problem, f"{name} @ {where}"))
    return ClientReport(spec, path, servers=tuple(servers), issues=tuple(issues))


def scan(
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
    project_dir: Path | None = None,
    clients: Sequence[ClientSpec] | None = None,
) -> tuple[ClientReport, ...]:
    """Scan every client.

    ``platform`` / ``environ`` / ``project_dir`` are all injectable: a machine
    that only runs Windows cannot exercise a macOS path, and a wrong path there
    is invisible to the user.
    """
    resolved_platform = platform or sys.platform
    resolved_environ = os.environ if environ is None else environ
    resolved_project = Path.cwd() if project_dir is None else project_dir
    specs = CLIENTS if clients is None else clients

    reports: list[ClientReport] = []
    for spec in specs:
        for path in candidate_paths(
            spec,
            platform=resolved_platform,
            environ=resolved_environ,
            project_dir=resolved_project,
        ):
            if path.is_file():
                reports.append(_read_client(spec, path))
                break
    return tuple(reports)


# ---------------------------------------------------------------- availability


def referenced_variables(entry: ServerEntry) -> tuple[str, ...]:
    """Environment variable names the entry references via ``${VAR}``, deduped and sorted.

    Only ``${NAME}`` is recognized, the one form common to every client's docs.
    VS Code's ``${input:xxx}`` is a different mechanism (prompted input).
    """
    found: set[str] = set()
    for value in (*entry.env.values(), *entry.headers.values()):
        found.update(_VARIABLE.findall(value))
    return tuple(sorted(found))


def static_issues(
    entry: ServerEntry, *, environ: Mapping[str, str] | None = None
) -> tuple[Issue, ...]:
    """Zero-I/O availability check: no processes, no requests.

    Covers most "configured but unreachable" cases instantly. ``environ`` is
    injectable so testing "missing variable" does not pollute the real
    environment.
    """
    env = os.environ if environ is None else environ
    issues: list[Issue] = []

    if entry.kind is not TransportKind.STDIO:
        issues.append(Issue(Problem.TRANSPORT_UNSUPPORTED, entry.kind.value))
        return tuple(issues)

    executable = entry.command[0]
    # Check as written first (it may already be absolute), then fall back to PATH.
    if shutil.which(executable) is None and not Path(executable).is_file():
        issues.append(Issue(Problem.COMMAND_MISSING, executable))

    missing = [name for name in referenced_variables(entry) if not env.get(name)]
    if missing:
        issues.append(Issue(Problem.ENV_MISSING, ", ".join(missing)))

    if entry.cwd and not Path(entry.cwd).is_dir():
        issues.append(Issue(Problem.CWD_MISSING, entry.cwd))

    return tuple(issues)


def tcp_probe(entry: ServerEntry, *, timeout: float = 1.0) -> tuple[Issue, ...]:
    """Probe the port only, sending no HTTP -- the HTTP transport is not implemented.

    Useful for local addresses: it separates "nothing is on that port" from
    "something else holds that port".
    """
    if not entry.url:
        return ()
    parsed = urlparse(entry.url)
    host = parsed.hostname
    if not host:
        return ()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return ()
    except TimeoutError:
        return (Issue(Problem.PORT_TIMEOUT, f"{host}:{port}"),)
    except ConnectionRefusedError:
        return (Issue(Problem.PORT_NOT_LISTENING, f"{host}:{port}"),)
    except OSError as exc:
        return (Issue(Problem.PORT_CLOSED, f"{host}:{port} · {exc.strerror or exc}"),)


#: Connect for real. Injected by the caller: ``services`` may not import
#: ``runtime`` at runtime (an upward dependency).
Prober = Callable[[ServerEntry], Iterable[Issue]]


def diagnose(
    reports: Iterable[ClientReport],
    *,
    probe: Prober | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[Diagnosis, ...]:
    """Verdict on every discovered server. Static only when ``probe`` is omitted."""
    out: list[Diagnosis] = []
    for report in reports:
        for entry in report.servers:
            issues = list(static_issues(entry, environ=environ))
            probed = False
            if probe is not None:
                # A stdio entry that already fails statically is not worth
                # connecting: that only hides the real cause behind a timeout.
                # URL entries always fail statically as "unsupported" yet their
                # port data is still useful, so those are probed regardless.
                if not issues or entry.kind is not TransportKind.STDIO:
                    issues.extend(probe(entry))
                    probed = True
            out.append(Diagnosis(entry, tuple(issues), probed=probed))
    return tuple(out)

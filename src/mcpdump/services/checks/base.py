"""Shared scaffolding for the conformance checks.

Adding a check means adding a file: export ``CHECKS`` from it and the registry
finds it automatically.

``spec`` is always English: it names a clause in the MCP specification, and
translating it would make it unverifiable. All other user-facing text goes
through ``i18n``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from ...core import MCPSession
from ...core.jsonrpc import (
    ProtocolError,
    decode,
    encode_notification,
    encode_request,
    is_response,
)
from ...core.session import ServerInfo
from ...core.transport import Transport, build_transport
from ...i18n import t

if TYPE_CHECKING:
    # Imported for annotations only. ``runtime`` is the assembly layer, above
    # ``services``; a runtime import would make ``services → runtime → ui →
    # services`` a cycle.
    from ...runtime import SessionOptions

#: Cap on retained evidence. A ``tools/list`` response can run to hundreds of KB;
#: keeping it whole wastes memory and drowns the report.
EVIDENCE_LIMIT = 2000

__all__ = [
    "BaseCheck",
    "Check",
    "CheckContext",
    "CheckResult",
    "Finding",
    "RawProbe",
    "Severity",
    "Status",
    "error",
    "json_evidence",
    "note",
    "warn",
]


class Severity(str, Enum):
    """Severity of a finding. Only ERROR makes a check fail."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class Status(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Finding:
    """One finding: a reason, plus reproducible evidence.

    Evidence is the raw message from the server, not a paraphrase, so it can be
    attached to a bug report as-is.
    """

    severity: Severity
    message: str
    evidence: str | None = None


def _clip(text: str | None) -> str | None:
    if text is None or len(text) <= EVIDENCE_LIMIT:
        return text
    return text[:EVIDENCE_LIMIT] + "…"


def json_evidence(payload: Any) -> str:
    """Compress structured data into single-line JSON for use as evidence.

    One line on purpose: evidence takes a single row in the report.
    """
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):  # pragma: no cover - fallback; normal data never gets here
        return repr(payload)


def error(message: str, evidence: str | None = None) -> Finding:
    return Finding(Severity.ERROR, message, _clip(evidence))


def warn(message: str, evidence: str | None = None) -> Finding:
    return Finding(Severity.WARNING, message, _clip(evidence))


def note(message: str, evidence: str | None = None) -> Finding:
    return Finding(Severity.INFO, message, _clip(evidence))


@dataclass
class CheckResult:
    """The conclusion of one check.

    ``status`` is derived rather than stored, which rules out a result marked as
    passing that still carries an ERROR finding.
    """

    id: str
    title: str
    spec: str
    findings: list[Finding] = field(default_factory=list)
    skipped: str | None = None

    @property
    def status(self) -> Status:
        if self.skipped is not None:
            return Status.SKIPPED
        if any(item.severity is Severity.ERROR for item in self.findings):
            return Status.FAILED
        return Status.PASSED

    @property
    def failed(self) -> bool:
        return self.status is Status.FAILED

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "spec": self.spec,
        }
        if self.skipped is not None:
            payload["skippedReason"] = self.skipped
        if self.findings:
            payload["findings"] = [
                {
                    "severity": item.severity.value,
                    "message": item.message,
                    "evidence": item.evidence,
                }
                for item in self.findings
            ]
        return payload


class RawProbe:
    """An unhandshaken connection: the transport is started, but the caller
    drives the handshake order.

    A check that needs to observe the handshake cannot use ``MCPSession``,
    because by then ``notifications/initialized`` has already gone out.
    """

    def __init__(self, opts: SessionOptions) -> None:
        self._opts = opts
        self._transport: Transport | None = None
        self._next_id = 1
        #: Messages received during the handshake window that were not responses
        #: to the current request; the ordering check depends on them.
        self.unsolicited: list[dict[str, Any]] = []
        #: Non-JSON-RPC text, verbatim.
        self.garbage: list[str] = []

    def __enter__(self) -> RawProbe:
        # ``launch_spec`` rather than ``server``: the latter is a display string,
        # and re-splitting it mangles paths with spaces. Must match
        # ``runtime.open_session``, or a server works under ``ls`` but not ``check``.
        self._transport = build_transport(
            self._opts.launch_spec, env=self._opts.env or None, cwd=self._opts.cwd
        )
        self._transport.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def _read_line(self, timeout: float) -> str | None:
        if self._transport is None:
            raise RuntimeError(t("check.probe_not_open"))
        return self._transport.recv(timeout=timeout)

    def send_request(self, method: str, params: dict[str, Any] | None = None) -> int:
        msg_id = self._next_id
        self._next_id += 1
        self._send(encode_request(msg_id, method, params))
        return msg_id

    def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send(encode_notification(method, params))

    def _send(self, line: str) -> None:
        if self._transport is None:
            raise RuntimeError(t("check.probe_not_open"))
        self._transport.send(line)

    def await_response(self, msg_id: int | str, timeout: float) -> dict[str, Any]:
        """Wait for the response with this id. Other traffic goes to
        ``unsolicited`` rather than being dropped.
        """
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(t("check.probe_timeout", timeout=timeout))
            line = self._read_line(remaining)
            if line is None:
                raise TimeoutError(t("check.probe_timeout", timeout=timeout))
            try:
                msg = decode(line)
            except ProtocolError:
                self.garbage.append(line)
                continue
            if is_response(msg) and msg.get("id") == msg_id:
                return msg
            self.unsolicited.append(msg)

    def drain(self, window: float) -> list[dict[str, Any]]:
        """Collect everything the server sends unprompted within ``window`` seconds.

        A fixed window rather than waiting until something arrives: a server that
        sends nothing is a normal outcome, and the check has to finish on time.
        """
        deadline = time.monotonic() + window
        collected: list[dict[str, Any]] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return collected
            line = self._read_line(remaining)
            if line is None:
                return collected
            try:
                collected.append(decode(line))
            except ProtocolError:
                self.garbage.append(line)


@dataclass
class CheckContext:
    """Everything a check needs for one run.

    Repeated probes are cached across checks: ``tools()`` issues ``tools/list``
    once, and ``smoke_call()`` makes one real call.
    """

    opts: SessionOptions
    session: MCPSession
    probe_timeout: float = 0.3

    _tools: list[dict[str, Any]] | None = field(default=None, init=False, repr=False)
    _smoke: tuple[dict[str, Any], str] | None = field(default=None, init=False, repr=False)
    _smoke_done: bool = field(default=False, init=False, repr=False)

    @property
    def server(self) -> ServerInfo:
        return self.session.server

    def tools(self) -> list[dict[str, Any]]:
        if self._tools is None:
            self._tools = self.session.list_tools()
        return self._tools

    def tool_names(self) -> list[str]:
        return [str(tool.get("name", "")) for tool in self.tools()]

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send a request. A JSON-RPC error from the server raises ``JsonRpcError``."""
        return self.session.request(method, params)

    def evidence(self) -> str | None:
        """The raw response line from the most recent round trip; that is the evidence."""
        exchange = self.session.last_exchange()
        return exchange.response_line if exchange else None

    def evidence_of(self, method: str) -> str | None:
        exchange = self.session.last_exchange(method)
        return exchange.response_line if exchange else None

    def smoke_call(self) -> tuple[dict[str, Any], str] | None:
        """Call a tool that needs no arguments, to get one real result sample.

        Two checks look at the shape of a result, so this is probed once. Returns
        ``None`` when no such tool exists.
        """
        if not self._smoke_done:
            self._smoke_done = True
            self._smoke = self._run_smoke()
        return self._smoke

    def _run_smoke(self) -> tuple[dict[str, Any], str] | None:
        for tool in self.tools():
            name = tool.get("name")
            if not name:
                continue
            schema = tool.get("inputSchema")
            if isinstance(schema, dict) and schema.get("required"):
                continue
            return self.session.call_tool(str(name), {}), str(name)
        return None

    @contextmanager
    def open_raw(self) -> Iterator[RawProbe]:
        """Open an unhandshaken connection; closed on exit."""
        probe = RawProbe(self.opts)
        with probe:
            yield probe


class BaseCheck:
    """Base class for a check. Subclasses provide ``id``, ``spec`` and ``run``.

    ``title`` is a method rather than a class attribute, because attributes are
    evaluated at import time and would pin the language. The text key is derived
    from ``id``, so the two cannot drift apart.
    """

    id: str = ""
    spec: str = ""

    def title(self) -> str:
        return t(self.title_key)

    @property
    def title_key(self) -> str:
        return f"check.{self.id.replace('-', '_')}.title"

    def run(self, ctx: CheckContext) -> CheckResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def result(self, *findings: Finding) -> CheckResult:
        """Assemble a result from findings; the presence of an ERROR decides pass or fail."""
        return CheckResult(self.id, self.title(), self.spec, list(findings))

    def skip(self, reason: str) -> CheckResult:
        return CheckResult(self.id, self.title(), self.spec, skipped=reason)


class Check(Protocol):
    """The check interface as the registry sees it.

    A ``Protocol`` rather than a base class: ``BaseCheck`` does not inherit from
    it, and checks share no implementation, so adding a check stays a matter of
    adding a file. Written the same way as ``core.transport.Transport``.
    """

    id: str
    spec: str

    def title(self) -> str: ...

    def run(self, ctx: CheckContext) -> CheckResult: ...

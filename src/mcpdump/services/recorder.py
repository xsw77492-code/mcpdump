"""Session recording format, and streaming read/write for it.

A single module because ``watch --record`` writes this format and ``replay`` /
``diff`` read it; defined twice, the two would drift. The file is JSONL: a header
line, then frames.

```text
{"format": "mcpdump-session", "version": 1, "at": "...", "argv": ["python", "server.py"]}
{"seq": 1, "atMs": 0.412, "direction": "to_server", "method": "initialize"}
{"seq": 2, "atMs": 12.8, "direction": "to_client", "method": "initialize"}
```

``version`` is not decoration: a replayer meeting an unknown version must refuse
rather than guess, since a false success is worse than an error. ``seq`` belongs
to the file, not to the proxy, so replay and diff trust the recorded order.

Reading is streamed because recordings over 100 MB are real; ``load_frames``
wraps ``iter_frames`` in ``list()`` for the cases that need random access.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from ..i18n import t

__all__ = [
    "DIRECTION_TO_CLIENT",
    "DIRECTION_TO_SERVER",
    "FORMAT_NAME",
    "FORMAT_VERSION",
    "RecordError",
    "RecordHeader",
    "RecordedFrame",
    "Recorder",
    "iter_frames",
    "load_frames",
    "load_header",
    "message_id",
    "message_method",
    "read_recording",
    "to_wire_event",
]

#: The ``format`` value on the first line. A different format gets a different
#: name, so an old replayer never reads a new file.
FORMAT_NAME = "mcpdump-session"

#: Format version. Adding a field does not bump it; changing a field's meaning does.
FORMAT_VERSION = 1

DIRECTION_TO_SERVER = "to_server"
DIRECTION_TO_CLIENT = "to_client"

#: Preview length for reading back; message lines are escaped in JSON, so ``\n``
#: becomes two characters.
_MAX_PREVIEW = 120


class RecordError(ValueError):
    """The recording cannot be read, or is not in a format this tool knows.

    Subclasses ``ValueError`` so the command layer can return a usage error
    rather than an environment error: a wrong filename and a missing Python are
    different problems.
    """


@dataclass(frozen=True)
class RecordHeader:
    """Recording metadata. ``argv`` is there so the server can be identified afterwards."""

    version: int
    at: str | None
    argv: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT_NAME,
            "version": self.version,
            "at": self.at,
            "argv": list(self.argv),
        }


@dataclass(frozen=True)
class RecordedFrame:
    """One recorded frame. Fields match the JSONL format exactly; none added or renamed."""

    seq: int
    at_ms: float
    direction: str
    method: str
    elapsed_ms: float | None
    line: str

    @property
    def to_server(self) -> bool:
        return self.direction == DIRECTION_TO_SERVER

    @property
    def is_error_response(self) -> bool:
        """A response carrying an ``error`` field is a failure.

        The JSON parse is lenient: the line was serialised from an already-parsed
        object, so a parse failure is treated as not-an-error rather than
        inventing changes that never happened.
        """
        if self.to_server:
            return False
        try:
            payload = json.loads(self.line)
        except ValueError:
            return False
        return isinstance(payload, dict) and "error" in payload

    @property
    def preview(self) -> str:
        """A single-line preview for the diff and error messages; newlines and
        length both break alignment.
        """
        text = " ".join(self.line.split())
        return text if len(text) <= _MAX_PREVIEW else text[: _MAX_PREVIEW - 1] + "…"

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "atMs": self.at_ms,
            "direction": self.direction,
            "method": self.method,
            "elapsedMs": self.elapsed_ms,
            "line": self.line,
        }


def to_wire_event(frame: RecordedFrame) -> Any:
    """Convert a recorded frame to ``core.proxy.ProxyEvent`` so per-frame rendering
    can reuse watch's visual language.

    The import is local: a top-level import would drag ``core.proxy`` into cases
    that only count JSONL lines, such as ``diff``.
    """
    from ..core.proxy import Direction, ProxyEvent

    direction = Direction.TO_SERVER if frame.to_server else Direction.TO_CLIENT
    return ProxyEvent(
        direction=direction,
        line=frame.line,
        method=frame.method,
        elapsed_ms=frame.elapsed_ms,
        sequence=frame.seq,
        at_ms=frame.at_ms,
    )


def _payload(line: str) -> dict[str, Any] | None:
    """Parse a frame into an object, or ``None`` if it will not parse or is not one.

    Bad frames are swallowed here: recordings are often hand-edited, and one
    truncated line should not abandon the whole analysis.
    """
    try:
        parsed = json.loads(line)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def message_id(line: str) -> Any:
    """Return the JSON-RPC ``id``, or ``None`` when there is not one.

    ``None`` and a literal ``null`` id must stay distinct: merging them would
    report every ordinary recording as holding unanswered requests.

    The test is whether the key is present, not whether the value is falsy:
    ``id: 0`` is a valid identifier.
    """
    payload = _payload(line)
    if payload is None or "id" not in payload:
        return None
    return payload["id"]


def message_method(line: str) -> str:
    """Return ``method``, or an empty string when there is none.

    The empty string rather than ``"?"`` lets callers tell a response (no method)
    from a broken frame.
    """
    payload = _payload(line)
    if payload is None:
        return ""
    method = payload.get("method")
    return method if isinstance(method, str) else ""


# ---------------------------------------------------------------- reading


def _parse_frame(raw: dict[str, Any], *, lineno: int) -> RecordedFrame:
    """Turn one JSON line into a ``RecordedFrame``, naming the line on any mismatch.

    The line number matters: recordings get hand-edited, and "bad format" alone
    leaves the user searching ten thousand lines.
    """

    def required(name: str, *kinds: type, label: str | None = None) -> Any:
        if name not in raw:
            raise RecordError(t("record.line_missing_field", lineno=lineno, name=name))
        value = raw[name]
        if not isinstance(value, kinds):
            expected = label or "/".join(kind.__name__ for kind in kinds)
            raise RecordError(
                t(
                    "record.line_wrong_type",
                    lineno=lineno,
                    name=name,
                    expected=expected,
                    actual=type(value).__name__,
                )
            )
        return value

    # ``bool`` subclasses ``int``, so ``isinstance(True, int)`` holds. A ``seq``
    # written as ``true`` is a typo and should not pass silently as 1.
    seq = required("seq", int)
    if isinstance(raw["seq"], bool):
        raise RecordError(t("record.seq_not_integer", lineno=lineno))

    elapsed = raw.get("elapsedMs")
    if elapsed is not None and not isinstance(elapsed, (int, float)):
        raise RecordError(t("record.elapsed_not_number", lineno=lineno))

    direction = required("direction", str)
    if direction not in (DIRECTION_TO_SERVER, DIRECTION_TO_CLIENT):
        raise RecordError(
            t(
                "record.bad_direction",
                lineno=lineno,
                to_server=DIRECTION_TO_SERVER,
                to_client=DIRECTION_TO_CLIENT,
                actual=direction,
            )
        )

    return RecordedFrame(
        seq=seq,
        at_ms=float(required("atMs", int, float, label=t("record.label.number"))),
        direction=direction,
        method=required("method", str),
        elapsed_ms=None if elapsed is None else float(elapsed),
        line=required("line", str),
    )


def _parse_header(raw: dict[str, Any], *, lineno: int) -> RecordHeader:
    if raw.get("format") != FORMAT_NAME:
        raise RecordError(
            t(
                "record.not_a_recording",
                lineno=lineno,
                format=FORMAT_NAME,
                found=raw.get("format"),
            )
        )
    version = raw.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise RecordError(t("record.version_not_integer", lineno=lineno))
    if version > FORMAT_VERSION:
        raise RecordError(
            t("record.version_too_new", version=version, supported=FORMAT_VERSION)
        )
    argv = raw.get("argv")
    return RecordHeader(
        version=version,
        at=raw.get("at") if isinstance(raw.get("at"), str) else None,
        argv=tuple(str(item) for item in argv) if isinstance(argv, list) else (),
    )


def _iter_json_lines(path: Path) -> Iterator[tuple[int, Any]]:
    """Read JSON line by line, skipping blanks, holding one line at a time."""
    with path.open("r", encoding="utf-8") as handle:
        for lineno, text in enumerate(handle, start=1):
            text = text.strip()
            if not text:
                continue
            try:
                yield lineno, json.loads(text)
            except ValueError as exc:
                raise RecordError(
                    t("record.line_not_json", lineno=lineno, error=exc)
                ) from exc


def load_header(path: str | Path) -> RecordHeader:
    """Read the first line. Raises ``RecordError`` if it is not a recording."""
    target = Path(path)
    try:
        for lineno, raw in _iter_json_lines(target):
            if not isinstance(raw, dict):
                raise RecordError(t("record.line_not_object", lineno=lineno))
            return _parse_header(raw, lineno=lineno)
    except OSError as exc:
        raise RecordError(
            t("record.unreadable", path=target, error=exc.strerror or exc)
        ) from exc
    raise RecordError(t("record.empty_file", path=target))


def iter_frames(path: str | Path) -> Iterator[RecordedFrame]:
    """Yield every frame as a stream; memory use is independent of file size.

    The header is skipped rather than parsed as a frame: it has no ``seq``, so
    parsing it would report a good recording as corrupt.
    """
    target = Path(path)
    try:
        seen_header = False
        for lineno, raw in _iter_json_lines(target):
            if not isinstance(raw, dict):
                raise RecordError(t("record.line_not_object", lineno=lineno))
            if not seen_header:
                _parse_header(raw, lineno=lineno)
                seen_header = True
                continue
            yield _parse_frame(raw, lineno=lineno)
    except OSError as exc:
        raise RecordError(
            t("record.unreadable", path=target, error=exc.strerror or exc)
        ) from exc


def load_frames(path: str | Path) -> list[RecordedFrame]:
    """Read every frame into memory, for cases needing random access: diff, sorting,
    statistics.
    """
    return list(iter_frames(path))


def read_recording(path: str | Path) -> tuple[RecordHeader, list[RecordedFrame]]:
    """The header plus every frame; ``diff`` needs both."""
    return load_header(path), load_frames(path)


# ---------------------------------------------------------------- writing


class _Writable(Protocol):
    """The minimum shape of a write target.

    A protocol rather than ``TextIO``, which also carries ``readline``, ``seek``
    and more that this module never calls; declaring the interface actually used
    lets a three-method stand-in represent a full disk in a test.
    """

    def write(self, text: str) -> int: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


class Recorder:
    """Write frames to JSONL as they arrive.

    Every line is flushed: ``watch`` usually ends with Ctrl-C, and anything still
    buffered disappears with the process. A write failure does not stop
    forwarding; it is recorded in ``error`` for the closing summary rather than
    hidden.
    """

    def __init__(self, path: str | Path, *, argv: tuple[str, ...] = ()) -> None:
        self.path = str(path)
        self.count = 0
        self.error: OSError | None = None
        self._handle: _Writable | None = None
        # Let a failure to open propagate: that is a usage error (a missing
        # directory) and the command should exit rather than run while recording
        # nothing.
        self._handle = self._open(self.path)
        header = RecordHeader(
            version=FORMAT_VERSION,
            at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            argv=argv,
        )
        try:
            self._handle.write(json.dumps(header.to_dict(), ensure_ascii=False) + "\n")
            self._handle.flush()
        except OSError as exc:
            self.error = exc
            self.close()

    def _open(self, path: str) -> _Writable:
        """Open the write target.

        ``newline="\\n"`` is required rather than default: left to the platform,
        Windows writes ``\\r\\n`` and the same session recorded on two machines
        differs throughout a diff. A method rather than inline code so a test can
        reach the "fails on first write" path.
        """
        return open(path, "w", encoding="utf-8", newline="\n")

    def write(self, frame: RecordedFrame) -> None:
        if self._handle is None:
            return
        try:
            self._handle.write(json.dumps(frame.to_dict(), ensure_ascii=False) + "\n")
            self._handle.flush()
        except OSError as exc:
            self.error = exc
            self.close()
            return
        self.count += 1

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.close()
        except OSError:
            pass

    def __enter__(self) -> Recorder:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

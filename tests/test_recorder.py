"""Tests for the recording format (``services/recorder.py``).

This is the single source of truth for the on-disk format: the round trip, the version
gate, broken-line line numbers, and genuine streaming in ``iter_frames``.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator

import pytest

from mcpdump.services.recorder import (
    DIRECTION_TO_CLIENT,
    DIRECTION_TO_SERVER,
    FORMAT_NAME,
    FORMAT_VERSION,
    RecordedFrame,
    Recorder,
    RecordError,
    iter_frames,
    load_frames,
    load_header,
    read_recording,
    to_wire_event,
)

# ---------------------------------------------------------------- building samples


def _frame(seq: int, *, to_server: bool = True, method: str = "tools/list") -> RecordedFrame:
    direction = DIRECTION_TO_SERVER if to_server else DIRECTION_TO_CLIENT
    payload: dict[str, object] = {"jsonrpc": "2.0", "method": method}
    if to_server:
        payload["id"] = seq
        params: dict[str, object] = {}
    else:
        payload["id"] = seq
        params = {}
    if not to_server:
        payload.pop("method")
        payload["result"] = {"ok": True}
    if to_server:
        payload["params"] = params
    line = json.dumps(payload, ensure_ascii=False)
    return RecordedFrame(
        seq=seq,
        at_ms=seq * 1.5,
        direction=direction,
        method=method,
        elapsed_ms=None if to_server else 2.5,
        line=line,
    )


def _write(path: pathlib.Path, frames: list[RecordedFrame], *, argv: tuple[str, ...] = ()) -> None:
    with Recorder(path, argv=argv) as recorder:
        for frame in frames:
            recorder.write(frame)


# ---------------------------------------------------------------- round trip


def test_written_frames_read_back_identically(tmp_path: pathlib.Path) -> None:
    """What goes in is what comes out: not one field extra, not one missing.

    Any "written as A, read back as B" drift turns this red, and that class of bug is
    invisible until someone replays a recording.
    """
    target = tmp_path / "session.jsonl"
    original = [_frame(1), _frame(2, to_server=False), _frame(3, method="tools/call")]
    _write(target, original, argv=("python", "server.py"))

    header, frames = read_recording(target)

    assert header.version == FORMAT_VERSION
    assert header.argv == ("python", "server.py")
    assert header.at is not None
    assert frames == original


def test_the_header_is_skipped_not_parsed_as_a_frame(tmp_path: pathlib.Path) -> None:
    """The header has no ``seq``. Parsing it as a frame would report "corrupt file"
    when the file is perfectly fine."""
    target = tmp_path / "session.jsonl"
    _write(target, [_frame(1)])

    frames = load_frames(target)

    assert len(frames) == 1
    assert frames[0].seq == 1


def test_seq_is_the_files_own_numbering(tmp_path: pathlib.Path) -> None:
    """``seq`` is read from the file only; never re-derived from the order.

    A recording may be concatenated pieces (``cat a.jsonl b.jsonl``) with non-contiguous
    numbering, and renumbering by position would erase that.
    """
    target = tmp_path / "session.jsonl"
    _write(target, [_frame(7), _frame(8), _frame(9)])

    assert [frame.seq for frame in load_frames(target)] == [7, 8, 9]


def test_blank_lines_are_tolerated(tmp_path: pathlib.Path) -> None:
    """Hand-edited files often keep blank lines. Reporting "not valid JSON" for one is
    pure nitpicking."""
    target = tmp_path / "session.jsonl"
    _write(target, [_frame(1)])
    text = target.read_text("utf-8")
    target.write_text(text.replace("\n", "\n\n"), encoding="utf-8")

    assert len(load_frames(target)) == 1


# ---------------------------------------------------------------- version gate


def test_a_newer_format_version_is_refused(tmp_path: pathlib.Path) -> None:
    """An unrecognised version must be refused, not guessed at best-effort.

    Guessing wrong means "what replays does not match what was recorded", and the user treats
    replay as evidence.
    """
    target = tmp_path / "future.jsonl"
    target.write_text(
        json.dumps({"format": FORMAT_NAME, "version": FORMAT_VERSION + 1}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RecordError, match="newer than"):
        load_header(target)


def test_an_older_format_version_is_accepted(tmp_path: pathlib.Path) -> None:
    """Older versions must stay readable — otherwise every new field forces users to
    re-record."""
    target = tmp_path / "old.jsonl"
    target.write_text(
        json.dumps({"format": FORMAT_NAME, "version": 0, "argv": []}) + "\n"
        + json.dumps(_frame(1).to_dict()) + "\n",
        encoding="utf-8",
    )

    assert load_header(target).version == 0


def test_a_foreign_file_is_refused_by_name(tmp_path: pathlib.Path) -> None:
    """Another tool's JSONL must be called out as "not an mcpdump recording", not
    reported as a pile of missing fields."""
    target = tmp_path / "other.jsonl"
    target.write_text('{"timestamp": 1, "level": "info"}\n', encoding="utf-8")

    with pytest.raises(RecordError, match=FORMAT_NAME):
        load_header(target)


# ---------------------------------------------------------------- broken lines


def test_a_broken_line_reports_its_linenumber(tmp_path: pathlib.Path) -> None:
    """The error must carry the line number. "Bad format" alone leaves the user
    searching ten thousand lines by hand."""
    target = tmp_path / "session.jsonl"
    with Recorder(target) as recorder:
        recorder.write(_frame(1))
        recorder.write(_frame(2))
        recorder.write(_frame(3))

    lines = target.read_text("utf-8").splitlines()
    lines[2] = lines[2].replace('"method": "tools/list"', '"method": 42')
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(RecordError, match="Line 3"):
        load_frames(target)


def test_a_missing_field_names_itself(tmp_path: pathlib.Path) -> None:
    """Report "which field is missing", not "this line is wrong" — the field is
    exactly what the user broke."""
    target = tmp_path / "session.jsonl"
    target.write_text(
        json.dumps({"format": FORMAT_NAME, "version": FORMAT_VERSION}) + "\n"
        + json.dumps({"seq": 1, "atMs": 0, "direction": DIRECTION_TO_SERVER, "line": "{}"})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RecordError, match="method"):
        load_frames(target)


def test_a_boolean_seq_is_not_silently_one(tmp_path: pathlib.Path) -> None:
    """``bool`` is a subclass of ``int``, so ``seq: true`` slips past ``isinstance``.

    ``true`` sorting alongside ``1`` would scramble the order of the recording.
    """
    target = tmp_path / "session.jsonl"
    target.write_text(
        json.dumps({"format": FORMAT_NAME, "version": FORMAT_VERSION}) + "\n"
        + json.dumps(
            {
                "seq": True,
                "atMs": 0,
                "direction": DIRECTION_TO_SERVER,
                "method": "x",
                "line": "{}",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RecordError, match="not an integer"):
        load_frames(target)


def test_an_unknown_direction_is_refused(tmp_path: pathlib.Path) -> None:
    """There are only two directions. A third value means the write side and the read
    side have drifted apart on the format."""
    target = tmp_path / "session.jsonl"
    target.write_text(
        json.dumps({"format": FORMAT_NAME, "version": FORMAT_VERSION}) + "\n"
        + json.dumps({"seq": 1, "atMs": 0, "direction": "sideways", "method": "x", "line": "{}"})
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RecordError, match="direction"):
        load_frames(target)


def test_an_empty_file_says_so(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "empty.jsonl"
    target.write_text("", encoding="utf-8")

    with pytest.raises(RecordError, match="empty file"):
        load_header(target)


def test_a_missing_file_reports_the_path(tmp_path: pathlib.Path) -> None:
    with pytest.raises(RecordError, match="Cannot read"):
        load_header(tmp_path / "nope.jsonl")


# ---------------------------------------------------------------- streaming


def test_iter_frames_holds_only_one_line_at_a_time(tmp_path: pathlib.Path) -> None:
    """``iter_frames`` is a generator: each ``next`` parses exactly one line.

    The method is "stop after the second item": with a real ``list`` the whole file would be
    in memory already, so the generator type and its laziness are what get tested.
    """
    target = tmp_path / "big.jsonl"
    _write(target, [_frame(seq) for seq in range(1, 501)])

    stream: Iterator[RecordedFrame] = iter_frames(target)
    assert next(stream).seq == 1
    assert next(stream).seq == 2
    # A generator has no length and was not consumed — it is still the unfinished one.
    assert not isinstance(stream, list)
    assert iter(stream) is stream


def test_load_frames_is_the_eager_version(tmp_path: pathlib.Path) -> None:
    """Use ``load_frames`` where random access is needed; it must agree with
    ``iter_frames``."""
    target = tmp_path / "session.jsonl"
    frames = [_frame(seq) for seq in range(1, 21)]
    _write(target, frames)

    assert load_frames(target) == list(iter_frames(target))


# ---------------------------------------------------------------- frame properties


def test_error_responses_are_recognised(tmp_path: pathlib.Path) -> None:
    """A "failure response" can only be judged from the ``error`` field, never from
    the direction."""
    failure = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32601}})
    ok = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})

    assert RecordedFrame(1, 0.0, DIRECTION_TO_CLIENT, "x", 1.0, failure).is_error_response
    assert not RecordedFrame(2, 0.0, DIRECTION_TO_CLIENT, "x", 1.0, ok).is_error_response
    assert not RecordedFrame(3, 0.0, DIRECTION_TO_SERVER, "x", None, ok).is_error_response


def test_an_unparsable_line_is_not_declared_an_error() -> None:
    """A broken message counts as not an error response.

    The other way (parse failure implies error) makes diff report changes that do not exist:
    one accidentally broken line would sprout a "response became an error" entry.
    """
    frame = RecordedFrame(1, 0.0, DIRECTION_TO_CLIENT, "x", 1.0, "{not json")

    assert not frame.is_error_response


def test_preview_is_one_line_and_bounded() -> None:
    """The preview is dropped into an aligned table, so newlines and excessive length
    both wreck the layout."""
    long_line = json.dumps({"jsonrpc": "2.0", "result": {"text": "x" * 400}})
    frame = RecordedFrame(1, 0.0, DIRECTION_TO_CLIENT, "x", 1.0, long_line)

    assert "\n" not in frame.preview
    assert len(frame.preview) <= 120
    assert frame.preview.endswith("…")

    embedded = RecordedFrame(2, 0.0, DIRECTION_TO_CLIENT, "x", 1.0, '{"a":\n"b"}')
    assert "\n" not in embedded.preview


def test_a_frame_converts_back_to_a_proxy_event() -> None:
    """``to_wire_event`` lets ``replay`` reuse ``watch``'s frame-by-frame view.

    When the two disagree, replay shows different symbols and directions than the recording;
    that is untrustworthy evidence, worse than an error.
    """
    from mcpdump.core.proxy import Direction

    outbound = to_wire_event(_frame(1))
    inbound = to_wire_event(_frame(2, to_server=False))

    assert outbound.direction is Direction.TO_SERVER
    assert inbound.direction is Direction.TO_CLIENT
    assert inbound.sequence == 2
    assert inbound.elapsed_ms == 2.5


# ---------------------------------------------------------------- writer behaviour


def test_every_frame_is_flushed_immediately(tmp_path: pathlib.Path) -> None:
    """One flush per line. ``watch`` usually ends with Ctrl-C, and a recording left in a
    buffer disappears with the process, losing exactly the last frames you want.
    """
    target = tmp_path / "session.jsonl"
    recorder = Recorder(target)
    recorder.write(_frame(1))

    # The process has not exited and ``close`` was not called; the frame is on disk.
    on_disk = target.read_text("utf-8").splitlines()
    assert len(on_disk) == 2  # header + 1 frame
    recorder.close()


def test_a_write_failure_is_recorded_without_stopping(tmp_path: pathlib.Path) -> None:
    """A failed write must not interrupt forwarding, but it must be recorded, not ignored.

    The real situation is a full disk; here the header is written normally, then every
    subsequent write fails — "the disk filled up halfway through recording".
    """
    recorder = Recorder(tmp_path / "session.jsonl")
    assert recorder.error is None

    recorder._handle = _BrokenFile()
    recorder.write(_frame(1))

    assert recorder.error is not None
    assert recorder.count == 0
    recorder.write(_frame(2))  # already closed: returns silently, does not raise


class _BrokenFile:
    """A handle stand-in that fails only on ``write``."""

    def write(self, _text: str) -> int:
        raise OSError(28, "No space left on device")

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_a_recorder_that_cannot_write_its_header_still_works(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A header that cannot be written must be noted, not raised out and ending the session.

    Failing to open is a usage error and the command layer exits; failing to write is an
    environment problem (full disk) worth forwarding through, so the assertion is "no raise".
    """
    monkeypatch.setattr(Recorder, "_open", lambda self, path: _BrokenFile())

    recorder = Recorder(tmp_path / "session.jsonl")  # no raise

    assert recorder.error is not None
    recorder.write(_frame(1))  # no raise
    assert recorder.count == 0


def test_close_is_idempotent(tmp_path: pathlib.Path) -> None:
    """Both ``finally`` and ``with`` call ``close``; the second call must not blow up."""
    recorder = Recorder(tmp_path / "session.jsonl")
    recorder.close()
    recorder.close()


def test_the_recorder_writes_lf_only(tmp_path: pathlib.Path) -> None:
    """``newline="\\n"`` is mandatory.

    Letting Python decide on Windows turns every line into ``\\r\\n``, and recording bytes are
    compared by diff, so the same session on two machines would look different everywhere.
    """
    target = tmp_path / "session.jsonl"
    _write(target, [_frame(1), _frame(2)])

    raw = target.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")


def test_unicode_payloads_survive_the_round_trip(tmp_path: pathlib.Path) -> None:
    """``ensure_ascii=False``: CJK in a recording must stay legible, not a sea of ``\\uXXXX``.

    The user is attaching this file to an issue for a human to read.
    """
    target = tmp_path / "session.jsonl"
    line = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"text": "天气"}},
        ensure_ascii=False,
    )
    frame = RecordedFrame(1, 0.0, DIRECTION_TO_SERVER, "tools/call", None, line)
    _write(target, [frame])

    assert "天气" in target.read_text("utf-8")
    assert load_frames(target)[0].line == frame.line

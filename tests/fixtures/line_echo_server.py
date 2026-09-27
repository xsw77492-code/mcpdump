"""The minimal, non-MCP peer used by the proxy tests: byte-level relaying, so any
reordering, dropped frame or re-encoding shows up in an assertion immediately.

``--mode`` picks echo / reply / sink; ``--burst N`` writes N lines and exits without
reading stdin, since otherwise the last frames of a dead child would be lost.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import BinaryIO


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="line_echo_server")
    parser.add_argument("--mode", choices=("echo", "reply", "sink"), default="echo")
    parser.add_argument("--emit", action="append", default=[], metavar="LINE")
    parser.add_argument("--stderr", default=None, metavar="TEXT")
    parser.add_argument("--burst", type=int, default=0, metavar="N")
    return parser


def _reply(out: BinaryIO, raw: bytes) -> None:
    """Answer every request that has an id. Notifications (no id) get no reply -- nor does MCP."""
    try:
        msg = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(msg, dict) or msg.get("id") is None:
        return
    payload = {"jsonrpc": "2.0", "id": msg["id"], "result": {"seen": msg.get("method")}}
    out.write(json.dumps(payload).encode("utf-8") + b"\n")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    out = sys.stdout.buffer

    if args.stderr:
        sys.stderr.write(args.stderr + "\n")
        sys.stderr.flush()

    for line in args.emit:
        out.write(line.encode("utf-8") + b"\n")
    out.flush()

    if args.burst:
        for index in range(args.burst):
            out.write(f'{{"burst":{index}}}\n'.encode())
        out.flush()
        return 0

    for raw in sys.stdin.buffer:
        if args.mode == "sink":
            continue
        if args.mode == "echo":
            out.write(raw)
        else:
            _reply(out, raw)
        out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

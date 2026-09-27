"""Generate ``docs/demo.svg`` from real ``mcpdump`` output.

    python docs/make_demo_svg.py

Every line in the drawing comes from running the CLI, so the image cannot drift
from the product. Re-run it whenever the output changes.

The server is launched through this interpreter (the only reliable way to find
it), but the panel's launch-command row is normalised to the literal
``python -m mcpdump.demo`` the README writes. Otherwise the image would carry
this machine's directory layout into a public README.

The reveal animation degrades to the fully drawn picture when SMIL is not
rendered, because every line's static ``opacity`` is already ``1``.
"""

from __future__ import annotations

import html
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "demo.svg"

#: What is actually launched — the bare name would resolve to whichever python
#: happens to be on PATH, which is not necessarily the one holding mcpdump.
LAUNCH = f'"{sys.executable}" -m mcpdump.demo'

#: What the panel shows, so the image carries no machine-specific path.
DISPLAY = "python -m mcpdump.demo"

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
ARROW = re.compile(r"^([→←⇢])\s+(\S+)(\s*)(.*)$")

#: Advance width of the font stack below at ``FONT_SIZE``. One number is enough
#: because every glyph in the output is monospace.
CHAR_W = 7.55
FONT_SIZE = 12.5
LINE_H = 19.0
PADDING = 24.0
BAR_H = 36.0
FONT = (
    "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
    "'Liberation Mono', 'Noto Sans Mono CJK SC', monospace"
)

BG = "#1e1e2e"
BAR = "#181825"
BORDER = "#313244"
FG = "#cdd6f4"
DIM = "#6c7086"
GREEN = "#a6e3a1"
BLUE = "#89b4fa"
TEAL = "#94e2d5"


#: Label column of the identity panel. A row that starts with none of these is a
#: continuation of the previous one — the launch command wraps on wide paths.
LABELS = ("transport", "protocol", "capabilities", "instructions")


def normalise_launch_command(rows: list[str]) -> list[str]:
    """Collapse the wrapped launch command into one row holding ``SERVER``.

    Only the rows directly under ``transport`` are dropped, so the ``instructions``
    row keeps its own wrapped remainder.
    """
    out: list[str] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        index += 1

        if not (row.startswith("│") and row.endswith("│")):
            out.append(row)
            continue

        interior = row[1:-1]
        if not interior.strip().startswith("transport"):
            out.append(row)
            continue

        plus = "    transport  stdio · " + DISPLAY
        out.append("│" + plus.ljust(len(interior)) + "│")

        while index < len(rows):
            nxt = rows[index]
            if not (nxt.startswith("│") and nxt.endswith("│")):
                break
            text = nxt[1:-1].strip()
            if not text or text.startswith(LABELS):
                break
            index += 1

    return out


def capture(*args: str) -> list[str]:
    """Run the CLI and return its stdout with ANSI stripped."""
    env = dict(os.environ)
    env["MCPDUMP_LANG"] = "en"
    proc = subprocess.run(
        [sys.executable, "-m", "mcpdump", *args, "--quiet-server"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
        env=env,
    )
    if proc.returncode != 0:
        raise SystemExit(f"mcpdump {' '.join(args)} exited {proc.returncode}:\n{proc.stderr}")
    return [ANSI.sub("", line).rstrip() for line in proc.stdout.splitlines()]


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def transcript() -> list[tuple[bool, str]]:
    """The two commands, trimmed to what a first-time reader needs."""
    listed = normalise_launch_command(capture("ls", LAUNCH))
    end = next(i for i, row in enumerate(listed) if row.startswith("Resources"))
    listed = listed[:end]

    called = capture("call", LAUNCH, "echo", "--args", '{"text":"hi"}')
    start = called.index("→ tools/call")
    summary = next(row for row in called if "round trip" in row)
    called = [*called[start : start + 4], "", summary, "", *called[-3:]]

    return [
        (True, 'mcpdump ls "python -m mcpdump.demo"'),
        *[(False, row) for row in listed],
        (False, ""),
        (True, 'mcpdump call "python -m mcpdump.demo" echo --args \'{"text":"hi"}\''),
        *[(False, row) for row in called],
    ]


def line_spans(line: str, *, prompt: bool) -> str:
    """One transcript line as coloured tspans."""
    if prompt:
        return f'<tspan fill="{GREEN}">$ </tspan><tspan fill="{BLUE}">{esc(line)}</tspan>'

    match = ARROW.match(line)
    if match:
        arrow, method, gap, rest = match.groups()
        spans = f'<tspan fill="{TEAL}">{esc(arrow)} {esc(method)}</tspan>'
        if rest or gap:
            spans += f'<tspan fill="{DIM}">{esc(gap)}{esc(rest)}</tspan>'
        return spans

    return f'<tspan fill="{FG}">{esc(line)}</tspan>'


def build(rows: list[tuple[bool, str]]) -> str:
    width = PADDING * 2 + max(len(text) for _, text in rows) * CHAR_W
    height = BAR_H + PADDING + len(rows) * LINE_H + PADDING

    body: list[str] = []
    y = BAR_H + PADDING + FONT_SIZE
    for index, (prompt, text) in enumerate(rows):
        body.append(
            f'<text x="{PADDING:.1f}" y="{y:.1f}" opacity="1" xml:space="preserve">'
            f"{line_spans(text, prompt=prompt)}"
            f'<animate attributeName="opacity" from="0" to="1" begin="{index * 55}ms" '
            f'dur="180ms" fill="freeze"/></text>'
        )
        y += LINE_H

    body.append(
        f'<rect x="{PADDING:.1f}" y="{y - FONT_SIZE:.1f}" width="{CHAR_W:.1f}" '
        f'height="{FONT_SIZE + 2:.1f}" fill="{FG}" opacity="1">'
        f'<animate attributeName="opacity" values="1;0;1" dur="1.2s" '
        f'repeatCount="indefinite" begin="{len(rows) * 55 + 400}ms"/></rect>'
    )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.1f} {height:.1f}" font-family="{FONT}" '
        f'font-size="{FONT_SIZE}">\n'
        f'  <rect width="{width:.1f}" height="{height:.1f}" rx="10" fill="{BG}" '
        f'stroke="{BORDER}"/>\n'
        f'  <path d="M0 10a10 10 0 0 1 10-10h{width - 20:.1f}a10 10 0 0 1 10 10v'
        f'{BAR_H - 10:.1f}H0z" fill="{BAR}"/>\n'
        f'  <circle cx="20" cy="18" r="5.5" fill="#f38ba8"/>\n'
        f'  <circle cx="40" cy="18" r="5.5" fill="#f9e2af"/>\n'
        f'  <circle cx="60" cy="18" r="5.5" fill="#a6e3a1"/>\n'
        f'  <text x="{width / 2:.1f}" y="22.5" fill="{DIM}" text-anchor="middle">'
        f"mcpdump · ls and call</text>\n"
        + "\n".join(f"  {element}" for element in body)
        + "\n</svg>\n"
    )


def main() -> None:
    rows = transcript()
    OUT.write_text(build(rows), encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(rows)} rows, {OUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()

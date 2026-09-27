"""Generate ``docs/demo.gif`` from real ``mcpdump`` output.

    python docs/make_demo_gif.py

GitHub renders SVG as a static image (SMIL animation is sandboxed out), so the
``demo.svg`` shows a crisp but frozen terminal capture on the README. This script
produces an animated GIF with the same content, colours and line-by-line reveal,
which GitHub does animate.

It reuses ``make_demo_svg`` for the transcript (the single source of truth for
what the demo shows), so the GIF and the SVG can never disagree.

Requires Pillow (``pip install pillow``). Only needed to regenerate the asset;
the committed GIF is what the README loads.
"""

from __future__ import annotations

import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

import make_demo_svg as svg  # noqa: E402


#: Pixel metrics. Monospace, so one advance width fits every glyph.
FONT_PX = 20
CHAR_W = 12
LINE_H = 27
PADDING = 20
BAR_H = 38
DOT_R = 6

#: Catppuccin Mocha, matching make_demo_svg so the two assets look identical.
BG = (30, 30, 46)
BAR = (24, 24, 37)
BORDER = (49, 50, 68)
FG = (205, 214, 244)
DIM = (108, 112, 134)
GREEN = (166, 227, 161)
BLUE = (137, 180, 250)
TEAL = (148, 226, 213)
RED = (243, 139, 168)
YELLOW = (249, 226, 175)


def _font() -> ImageFont.FreeTypeFont:
    """A monospace font at FONT_PX, falling back across common Windows paths."""
    for name in ("consola.ttf", "cour.ttf", "lucon.ttf"):
        try:
            return ImageFont.truetype(name, FONT_PX)
        except OSError:
            continue
    return ImageFont.load_default()


def _colour_for(line: str, prompt: bool) -> tuple[int, int, int]:
    if prompt:
        return GREEN
    match = svg.ARROW.match(line)
    if match:
        return TEAL
    return FG


def _render_frame(rows: list[tuple[bool, str]], visible: int) -> Image.Image:
    width = PADDING * 2 + max(len(text) for _, text in rows) * CHAR_W
    height = BAR_H + PADDING + len(rows) * LINE_H + PADDING
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)
    font = _font()

    # Title bar.
    draw.rectangle([0, 0, width, BAR_H], fill=BAR)
    draw.ellipse([18 - DOT_R, BAR_H // 2 - DOT_R, 18 + DOT_R, BAR_H // 2 + DOT_R], fill=RED)
    draw.ellipse([38 - DOT_R, BAR_H // 2 - DOT_R, 38 + DOT_R, BAR_H // 2 + DOT_R], fill=YELLOW)
    draw.ellipse([58 - DOT_R, BAR_H // 2 - DOT_R, 58 + DOT_R, BAR_H // 2 + DOT_R], fill=GREEN)
    title = "mcpdump · ls and call"
    draw.text((width // 2, BAR_H // 2), title, font=font, fill=DIM, anchor="mm")
    draw.rectangle([0, 0, width - 1, height - 1], outline=BORDER, width=1)

    y = BAR_H + PADDING
    for index, (prompt, text) in enumerate(rows):
        if index >= visible:
            break
        if prompt:
            draw.text((PADDING, y), "$ " + text, font=font, fill=BLUE)
        else:
            draw.text((PADDING, y), text, font=font, fill=_colour_for(text, False))
        y += LINE_H

    return img


def main() -> None:
    rows = svg.transcript()
    frames: list[Image.Image] = []
    for visible in range(1, len(rows) + 1):
        frames.append(_render_frame(rows, visible))
    # Hold the final frame so the reader can absorb the result.
    for _ in range(30):
        frames.append(_render_frame(rows, len(rows)))

    out = svg.OUT.parent / "demo.gif"
    frames[0].save(
        out,
        save_all=True,
        append_images=frames[1:],
        duration=55,
        loop=0,
        optimize=True,
    )
    print(f"{out.relative_to(svg.ROOT)}: {len(frames)} frames, {out.stat().st_size} bytes")


if __name__ == "__main__":
    main()

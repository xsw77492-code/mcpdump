"""Conformance badge: a static shields.io URL in one line of Markdown.

No third-party library and no hosting service: the grade lives entirely in the
URL's text and colour. The link carries campaign parameters, so it is possible
to tell which README the traffic came from.
"""

from __future__ import annotations

from urllib.parse import quote

from ..i18n import t
from .runner import CheckReport

__all__ = ["HOMEPAGE", "badge_markdown"]

#: Landing page for the badge link: the repository home, where a click from
#: someone else's README should end up.
HOMEPAGE = "https://github.com/xsw77492-code/mcpdump"

#: Colour by pass ratio: perfect, good, passing, failing.
_COLOR_STEPS: tuple[tuple[float, str], ...] = (
    (1.0, "brightgreen"),
    (0.8, "green"),
    (0.6, "yellow"),
    (0.0, "red"),
)


def _ratio(report: CheckReport) -> float:
    return report.passed / report.total if report.total else 0.0


def _color(ratio: float) -> str:
    for threshold, color in _COLOR_STEPS:
        if ratio >= threshold:
            return color
    return "red"  # pragma: no cover - the thresholds already cover [0, 1]


def _segment(text: str) -> str:
    """Escape a shields.io path segment; an unescaped space or slash changes the
    badge's meaning.
    """
    return quote(text, safe="")


def badge_markdown(report: CheckReport, *, link: str = HOMEPAGE) -> str:
    """Build one line of Markdown that can be pasted straight into a README."""
    label = t("badge.label")
    message = report.score
    image = (
        f"https://img.shields.io/badge/"
        f"{_segment(label)}-{_segment(message)}-{_color(_ratio(report))}"
    )
    target = f"{link}?utm_source=badge&utm_medium=readme&utm_campaign=conformance"
    return f"[![{label} {message}]({image})]({target})"

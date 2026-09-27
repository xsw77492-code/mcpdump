"""Unit tests for the rendering primitives in ``ui.render``.

Only ``format_duration`` is covered here: its boundaries all sit on unit transitions. The
other ``render_*`` helpers write to a console and are exercised by the CLI tests.
"""

from __future__ import annotations

import pytest

from mcpdump.ui import format_duration


class TestMilliseconds:
    """Below one second the unit stays milliseconds."""

    def test_sub_millisecond_keeps_one_decimal(self) -> None:
        assert format_duration(0.04) == "0.0 ms"

    def test_ordinary_latency(self) -> None:
        assert format_duration(12.3) == "12.3 ms"

    def test_just_below_the_second(self) -> None:
        assert format_duration(999.9) == "999.9 ms"

    def test_rounds_up_into_the_next_unit_instead_of_printing_1000_ms(self) -> None:
        """999.99 ms rounds to 1000.0 ms, which reads as a missing carry."""
        assert format_duration(999.99) == "1.00 s"


class TestSeconds:
    def test_exactly_one_second(self) -> None:
        assert format_duration(1_000.0) == "1.00 s"

    def test_keeps_two_decimals(self) -> None:
        assert format_duration(12_345.0) == "12.34 s"

    def test_rounds_up_into_minutes_instead_of_printing_60_s(self) -> None:
        """59.999 s rounds to 60.00 s, which reads as minutes failing to carry."""
        assert format_duration(59_999.0) == "1m 0.0s"


class TestMinutes:
    def test_exactly_one_minute(self) -> None:
        assert format_duration(60_000.0) == "1m 0.0s"

    def test_typical_watch_session(self) -> None:
        """The real scale of a watch session: a little over three minutes."""
        assert format_duration(183_472.5) == "3m 3.5s"

    def test_rounds_up_into_hours_instead_of_printing_60m(self) -> None:
        assert format_duration(3_599_999.0) == "1h 00m"


class TestHours:
    def test_exactly_one_hour(self) -> None:
        assert format_duration(3_600_000.0) == "1h 00m"

    def test_pads_minutes(self) -> None:
        """The hour tier drops seconds but zero-pads minutes: "1h 5m" and "1h 50m" would
        otherwise be easy to confuse.
        """
        assert format_duration(7_325_000.0) == "2h 02m"

    def test_drops_seconds_entirely(self) -> None:
        assert format_duration(5_400_000.0) == "1h 30m"


class TestOutOfRange:
    def test_negative_is_clamped(self) -> None:
        """A monotonic-clock difference should not be negative; the sign must never
        reach the user.
        """
        assert format_duration(-3.0) == "0.0 ms"

    def test_zero(self) -> None:
        assert format_duration(0.0) == "0.0 ms"

    @pytest.mark.parametrize("value", [0.0, 12.3, 1_000.0, 60_000.0, 3_600_000.0])
    def test_every_unit_is_a_non_empty_string(self, value: float) -> None:
        assert format_duration(value).strip()

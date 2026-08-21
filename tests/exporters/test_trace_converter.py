import pytest

from gcmon.exporters.trace_converter import duration_text, seen_text


class TestDurationText:
    """The readable half of a pause total.

    Exactness lives in the `_ns` arg beside it; this one only has to be read
    correctly at a glance, which the digits are not.
    """

    @pytest.mark.parametrize(
        ("ns", "text"),
        [
            (3_316_458_100, "3s 316ms 458µs 100ns"),
            (5_000_000, "5ms"),
            (200, "200ns"),
            (1_000_000_100, "1s 100ns"),
            (90_000_000_000, "1m 30s"),
            (3_600_000_000_000, "1h"),
            (0, "0ns"),
        ],
    )
    def test_it_reads_as_a_duration(self, ns: int, text: str) -> None:
        assert duration_text(ns) == text

    def test_the_units_multiply_back_to_the_nanoseconds(self) -> None:
        """Every unit a component carries, against the number it came from.
        A wrong divisor produces text that still looks like a duration."""
        sizes = {"h": 3_600_000_000_000, "m": 60_000_000_000, "s": 1_000_000_000, "ms": 1_000_000, "µs": 1_000}

        for ns in (1, 999, 1_000, 3_316_458_100, 86_400_000_000_123):
            total = 0
            for part in duration_text(ns).split():
                digits = part.rstrip("hmsnµ")
                total += int(digits) * sizes.get(part.removeprefix(digits), 1)
            assert total == ns


class TestSeenText:
    """How much of an interval gcmon read, for a reader deciding whether to
    trust the bar's neighbours."""

    @pytest.mark.parametrize(
        ("observed", "lost", "text"),
        [
            (47, 7, "87.0% (47 of 54)"),
            (0, 5, "0.0% (0 of 5)"),
            (9, 0, "100.0% (9 of 9)"),
            (1, 2, "33.3% (1 of 3)"),
        ],
    )
    def test_it_reads_as_a_share_of_a_total(self, observed: int, lost: int, text: str) -> None:
        assert seen_text(observed, lost) == text

    def test_an_empty_interval_divides_by_nothing(self) -> None:
        """No collection ran and none was lost. A loss record never carries
        this, but the helper must not raise on the way to finding that out."""
        assert seen_text(0, 0) == "100.0% (0 of 0)"

    def test_the_total_is_what_ran_not_what_was_read(self) -> None:
        """The denominator is the reason this is worth writing out: a bare
        percentage says how bad the blindness was, not how much there was to
        be blind about."""
        assert seen_text(2, 98).endswith("(2 of 100)")

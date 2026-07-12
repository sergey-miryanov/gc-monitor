"""Tests for Stats class."""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from gcmon.stats import HAS_DDSKETCH, Stats


class TestStatsUpdate:
    """Tests for Stats.update method."""

    def test_update_increments_count(self, stats: Stats) -> None:
        stats.update(100.0)
        assert stats.count() == 1

        stats.update(200.0)
        assert stats.count() == 2

    def test_update_accumulates_sum(self, stats: Stats) -> None:
        stats.update(100.0)
        assert stats.sum() == 100.0

        stats.update(250.0)
        assert stats.sum() == 350.0

    def test_update_appends_to_buffer(self, stats: Stats) -> None:
        stats.update(42.0)
        assert len(stats.buffer) == 1
        assert 42.0 in stats.buffer

    @pytest.mark.skipif(not HAS_DDSKETCH, reason="ddsketch not installed")
    def test_update_with_sketch(self, stats: Stats) -> None:
        assert stats.has_sketch
        mock_sketch = MagicMock()
        stats._sketch = mock_sketch

        stats.update(150.0)
        mock_sketch.add.assert_called_once_with(150.0)

    def test_update_without_sketch(self, stats_without_ddsketch: Stats) -> None:
        assert not stats_without_ddsketch.has_sketch
        stats_without_ddsketch.update(150.0)
        assert stats_without_ddsketch.count() == 1
        assert stats_without_ddsketch.sum() == 150.0


class TestStatsMaterialize:
    """Tests for Stats.materialize method."""

    def test_materialize_computes_percentiles(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        p = stats_with_data.percentiles
        assert p is not None
        assert 50 in p
        assert 90 in p
        assert 95 in p
        assert 99 in p

    def test_materialize_percentile_values_correct(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        p = stats_with_data.percentiles
        assert p is not None
        assert p[50] == 300.0

    def test_materialize_clears_buffer(self, stats_with_data: Stats) -> None:
        assert len(stats_with_data.buffer) == 5
        stats_with_data.materialize()
        assert len(stats_with_data.buffer) == 0

    def test_materialize_disables_sketch(self, stats: Stats) -> None:
        for i in range(10):
            stats.update(float(i))
        assert stats.has_sketch
        stats.materialize()
        assert not stats.has_sketch

    def test_update_after_materialize_raises(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        with pytest.raises(RuntimeError, match="Cannot update Stats after materialize"):
            stats_with_data.update(999.0)

    def test_materialize_called_twice_is_noop(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        assert stats_with_data.percentiles is not None
        first_percentiles = stats_with_data.percentiles.copy()
        stats_with_data.materialize()
        assert stats_with_data.percentiles == first_percentiles

    def test_materialize_empty_stats(self, stats: Stats) -> None:
        assert stats.count() == 0
        stats.materialize()
        assert stats.percentiles is None


class TestStatsAverage:
    """Tests for Stats.average method."""

    def test_average_empty(self, stats: Stats) -> None:
        assert stats.average() == 0.0

    def test_average_single_value(self, stats: Stats) -> None:
        stats.update(42.0)
        assert stats.average() == 42.0

    def test_average_multiple_values(self, stats: Stats) -> None:
        stats.update(100.0)
        stats.update(200.0)
        stats.update(300.0)
        assert stats.average() == 200.0


class TestStatsPercentile:
    """Tests for Stats.percentile method."""

    def test_percentile_from_materialized(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        assert stats_with_data.percentile(50) == 300.0

    def test_percentile_unknown_returns_zero_after_materialize(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        assert stats_with_data.percentile(25) == 0.0

    def test_percentile_from_buffer(self, stats: Stats) -> None:
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            stats.update(v)
        p50 = stats.percentile(50)
        assert abs(p50 - 30.0) < 1e-10

    @pytest.mark.skipif(not HAS_DDSKETCH, reason="ddsketch not installed")
    def test_percentile_from_sketch_when_buffer_full(self, stats: Stats) -> None:
        for i in range(Stats.MAX_BUFFER_LEN + 100):
            stats.update(float(i))
        p50 = stats.percentile(50)
        assert p50 > 0.0

    def test_percentile_empty_returns_zero(self, stats: Stats) -> None:
        assert stats.percentile(50) == 0.0

    def test_percentile_single_value_buffer(self, stats: Stats) -> None:
        stats.update(42.0)
        assert stats.percentile(50) == 42.0
        assert stats.percentile(0) == 42.0
        assert stats.percentile(100) == 42.0

    def test_percentile_unknown_returns_zero_for_arbitrary_value(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        assert stats_with_data.percentile(33) == 0.0


class TestStatsCountAndSum:
    """Tests for Stats.count and Stats.sum methods."""

    def test_count_initial(self, stats: Stats) -> None:
        assert stats.count() == 0

    def test_count_after_updates(self, stats: Stats) -> None:
        for i in range(5):
            stats.update(float(i))
        assert stats.count() == 5

    def test_sum_initial(self, stats: Stats) -> None:
        assert stats.sum() == 0.0

    def test_sum_after_updates(self, stats: Stats) -> None:
        stats.update(10.0)
        stats.update(20.0)
        stats.update(30.0)
        assert stats.sum() == 60.0


class TestStatsBufferLimit:
    """Tests for Stats buffer size limit."""

    def test_buffer_respects_maxlen(self, stats: Stats) -> None:
        for i in range(Stats.MAX_BUFFER_LEN + 1000):
            stats.update(float(i))
        assert len(stats.buffer) == Stats.MAX_BUFFER_LEN

    def test_buffer_count_not_affected_by_limit(self, stats: Stats) -> None:
        total_updates = Stats.MAX_BUFFER_LEN + 500
        for _ in range(total_updates):
            stats.update(1.0)
        assert stats.count() == total_updates

    def test_buffer_sum_not_affected_by_limit(self, stats: Stats) -> None:
        total_updates = Stats.MAX_BUFFER_LEN + 500
        for _ in range(total_updates):
            stats.update(2.0)
        assert stats.sum() == float(total_updates) * 2.0


class TestStatsNonNumbers:
    def test_update_nan(self, stats: Stats) -> None:
        stats.update(float("nan"))
        assert stats.count() == 1
        assert math.isnan(stats.sum())

    def test_update_inf_without_ddsketch(self, stats_without_ddsketch: Stats) -> None:
        stats_without_ddsketch.update(float("inf"))
        assert stats_without_ddsketch.count() == 1
        assert stats_without_ddsketch.sum() == float("inf")
        assert stats_without_ddsketch.average() == float("inf")


class TestStatsPercentileValidation:
    """Tests for Stats.percentile input validation (BUG-32)."""

    def test_negative_raises_value_error(self, stats_with_data: Stats) -> None:
        with pytest.raises(ValueError, match=r"percentile must be in \[0, 100\]"):
            stats_with_data.percentile(-1)

    def test_zero_is_valid(self, stats_with_data: Stats) -> None:
        result = stats_with_data.percentile(0)
        assert result == 100.0

    def test_hundred_is_valid(self, stats_with_data: Stats) -> None:
        result = stats_with_data.percentile(100)
        assert result == 500.0

    def test_above_hundred_raises_value_error(self, stats_with_data: Stats) -> None:
        with pytest.raises(ValueError, match=r"percentile must be in \[0, 100\]"):
            stats_with_data.percentile(101)

    def test_far_above_hundred_raises_value_error(self, stats_with_data: Stats) -> None:
        with pytest.raises(ValueError, match=r"percentile must be in \[0, 100\]"):
            stats_with_data.percentile(9999)

    def test_negative_raises_on_materialized_stats(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        with pytest.raises(ValueError, match=r"percentile must be in \[0, 100\]"):
            stats_with_data.percentile(-50)

    def test_above_hundred_raises_on_materialized_stats(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        with pytest.raises(ValueError, match=r"percentile must be in \[0, 100\]"):
            stats_with_data.percentile(150)

    def test_negative_raises_on_empty_stats(self, stats: Stats) -> None:
        with pytest.raises(ValueError, match=r"percentile must be in \[0, 100\]"):
            stats.percentile(-1)

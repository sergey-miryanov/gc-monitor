"""Tests for Stats class."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gc_monitor.stats import HAS_DDSKETCH, Stats


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
        assert len(stats._data) == 1
        assert 42.0 in stats._data

    @pytest.mark.skipif(not HAS_DDSKETCH, reason="ddsketch not installed")
    def test_update_with_sketch(self, stats: Stats) -> None:
        assert stats._sketch is not None
        mock_sketch = MagicMock()
        stats._sketch = mock_sketch

        stats.update(150.0)
        mock_sketch.add.assert_called_once_with(150.0)

    def test_update_without_sketch(self, stats_without_ddsketch: Stats) -> None:
        assert stats_without_ddsketch._sketch is None
        stats_without_ddsketch.update(150.0)
        assert stats_without_ddsketch.count() == 1
        assert stats_without_ddsketch.sum() == 150.0


class TestStatsMaterialize:
    """Tests for Stats.materialize method."""

    def test_materialize_computes_percentiles(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        assert stats_with_data._percentiles is not None
        assert 50 in stats_with_data._percentiles
        assert 90 in stats_with_data._percentiles
        assert 95 in stats_with_data._percentiles
        assert 99 in stats_with_data._percentiles

    def test_materialize_percentile_values_correct(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        assert stats_with_data._percentiles is not None
        assert stats_with_data._percentiles[50] == 300.0

    def test_materialize_clears_buffer(self, stats_with_data: Stats) -> None:
        assert len(stats_with_data._data) == 5
        stats_with_data.materialize()
        assert len(stats_with_data._data) == 0

    def test_materialize_disables_sketch(self, stats: Stats) -> None:
        for i in range(10):
            stats.update(float(i))
        assert stats._sketch is not None
        stats.materialize()
        assert stats._sketch is None

    def test_update_after_materialize_raises(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        with pytest.raises(RuntimeError, match="Cannot update Stats after materialize"):
            stats_with_data.update(999.0)

    def test_materialize_called_twice_is_noop(self, stats_with_data: Stats) -> None:
        stats_with_data.materialize()
        first_percentiles = stats_with_data._percentiles.copy()
        stats_with_data.materialize()
        assert stats_with_data._percentiles == first_percentiles

    def test_materialize_empty_stats(self, stats: Stats) -> None:
        assert stats.count() == 0
        stats.materialize()
        assert stats._percentiles is None


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

    def test_percentile_unknown_returns_zero_after_materialize(
        self, stats_with_data: Stats
    ) -> None:
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
        assert len(stats._data) == Stats.MAX_BUFFER_LEN

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

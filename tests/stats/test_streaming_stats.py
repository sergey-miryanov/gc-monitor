"""Tests for streaming_stats module."""

from collections.abc import Callable

import numpy as np

from gcmon.data import GCStatsInfo
from gcmon.protocol import TGCStatsInfo
from gcmon.stats import StreamingStats, get_quantile_value

TOLERANCE = 1e-12


class TestGetQuantileValue:
    """Tests for get_quantile_value function."""

    def test_empty(self) -> None:
        assert get_quantile_value([], 50) == 0.0
        assert get_quantile_value([], 90) == 0.0
        assert get_quantile_value([], 95) == 0.0
        assert get_quantile_value([], 99) == 0.0

    def test_single_element(self) -> None:
        assert get_quantile_value([42.0], 50) == 42.0
        assert get_quantile_value([42.0], 0) == 42.0
        assert get_quantile_value([42.0], 100) == 42.0
        assert get_quantile_value([42.0], 90) == 42.0
        assert get_quantile_value([42.0], 95) == 42.0
        assert get_quantile_value([42.0], 99) == 42.0

    def test_two_elements(self) -> None:
        data = [10.0, 20.0]
        for p in [0, 50, 90, 95, 99, 100]:
            result = get_quantile_value(data, p)
            expected = float(np.percentile(data, p, method="linear"))
            assert abs(result - expected) < TOLERANCE

    def test_three_elements(self) -> None:
        data = [1.0, 2.0, 3.0]
        for p in [0, 25, 50, 75, 90, 95, 99, 100]:
            result = get_quantile_value(data, p)
            expected = float(np.percentile(data, p, method="linear"))
            assert abs(result - expected) < TOLERANCE

    def test_matches_numpy_linear(self) -> None:
        data = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        for p in [0, 10, 25, 50, 75, 90, 95, 99, 100]:
            result = get_quantile_value(data, p)
            expected = float(np.percentile(data, p, method="linear"))
            assert abs(result - expected) < TOLERANCE

    def test_random_data_matches_numpy(self) -> None:
        rng = np.random.default_rng(42)
        for _ in range(20):
            values = sorted(rng.uniform(0, 1000, size=500).tolist())
            for p in [5, 10, 25, 50, 75, 90, 95, 99]:
                result = get_quantile_value(values, p)
                expected = float(np.percentile(values, p, method="linear"))
                assert abs(result - expected) < TOLERANCE


class TestStreamingStatsUpdate:
    """Tests for StreamingStats.update method."""

    def test_update_increments_count(
        self,
        streaming_stats: StreamingStats,
        mock_stats_item: TGCStatsInfo,
    ) -> None:
        streaming_stats.update(12345, mock_stats_item)
        assert streaming_stats.count() == 1

        streaming_stats.update(12345, mock_stats_item)
        assert streaming_stats.count() == 2

    def test_update_records_pause_metric(
        self,
        streaming_stats: StreamingStats,
        mock_stats_item: TGCStatsInfo,
    ) -> None:
        streaming_stats.update(12345, mock_stats_item)
        assert streaming_stats.metrics["pause"][0].count() == 1

    def test_update_records_incremental_metrics(
        self,
        streaming_stats: StreamingStats,
        incremental_gc_stats_item: GCStatsInfo,
    ) -> None:
        streaming_stats.update(12345, incremental_gc_stats_item)
        assert streaming_stats.metrics["mark_alive"][0].count() == 1
        assert streaming_stats.metrics["fill_increment"][0].count() == 1
        assert streaming_stats.metrics["deduce_unreachable"][0].count() == 1
        assert streaming_stats.metrics["handle_weakrefs"][0].count() == 1
        assert streaming_stats.metrics["finalize_garbage"][0].count() == 1
        assert streaming_stats.metrics["handle_resurrected"][0].count() == 1
        assert streaming_stats.metrics["clear_weakrefs"][0].count() == 1
        assert streaming_stats.metrics["delete_garbage"][0].count() == 1

    def test_update_skips_zero_duration(
        self,
        streaming_stats: StreamingStats,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        item = gc_stats_item_factory(ts_start=1000, ts_stop=1000)
        streaming_stats.update(12345, item)
        assert streaming_stats.metrics["pause"][0].count() == 0

    def test_update_keeps_sub_microsecond_duration(
        self,
        streaming_stats: StreamingStats,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        """Durations are stored in nanoseconds. They used to be truncated to
        microseconds on ingest, so a sub-microsecond phase counted as 0."""
        item = gc_stats_item_factory(ts_start=0, ts_stop=750)
        streaming_stats.update(12345, item)

        assert streaming_stats.metrics["pause"][0].count() == 1
        assert streaming_stats.metrics["pause"][0].sum() == 750

    def test_update_tracks_heap_size(
        self,
        streaming_stats: StreamingStats,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        item1 = gc_stats_item_factory(heap_size=1_000_000)
        item2 = gc_stats_item_factory(heap_size=5_000_000)
        streaming_stats.update(12345, item1)
        streaming_stats.update(12345, item2)
        assert streaming_stats._heap_size[12345] == 5_000_000

    def test_update_heap_size_is_max_per_pid(
        self,
        streaming_stats: StreamingStats,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        item_small = gc_stats_item_factory(heap_size=100)
        item_large = gc_stats_item_factory(heap_size=500)
        streaming_stats.update(12345, item_large)
        streaming_stats.update(12345, item_small)
        assert streaming_stats._heap_size[12345] == 500


class TestStreamingStatsPidTracking:
    """Tests for StreamingStats PID tracking."""

    def test_pids_returns_all_tracked_pids(self, streaming_stats_with_pids: StreamingStats) -> None:
        pids = streaming_stats_with_pids.pids()
        assert pids == {11111, 22222, 33333}

    def test_get_pid_stats_returns_active(self, streaming_stats_with_pids: StreamingStats) -> None:
        pid_stats = streaming_stats_with_pids.get_pid_stats(11111)
        assert pid_stats is not None
        assert "pause" in pid_stats

    def test_get_pid_stats_returns_materialized(
        self,
        streaming_stats: StreamingStats,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        for pid in range(StreamingStats.MAX_ACTIVE_PIDS + 1):
            streaming_stats.update(pid, gc_stats_item_factory())

        old_pid = 0
        pid_stats = streaming_stats.get_pid_stats(old_pid)
        assert pid_stats is not None

    def test_get_pid_stats_missing_returns_none(self, streaming_stats: StreamingStats) -> None:
        assert streaming_stats.get_pid_stats(99999) is None

    def test_per_pid_pause_recorded_once(
        self,
        streaming_stats: StreamingStats,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        """The per-PID 'pause' metric is recorded once per event, matching the
        global total. It used to be recorded twice, doubling Count/Sum/Avg in
        the per-PID rows of the --stats table."""
        streaming_stats.update(12345, gc_stats_item_factory(ts_start=1_000, ts_stop=6_000))

        pid_stats = streaming_stats.get_pid_stats(12345)
        assert pid_stats is not None
        assert pid_stats["pause"][0].count() == 1
        assert pid_stats["pause"][0].sum() == 5_000
        assert pid_stats["pause"][0].count() == streaming_stats.metrics["pause"][0].count()
        assert pid_stats["pause"][0].sum() == streaming_stats.metrics["pause"][0].sum()

    def test_per_pid_metrics_match_totals_for_single_pid(
        self,
        streaming_stats: StreamingStats,
        incremental_gc_stats_item: GCStatsInfo,
    ) -> None:
        """With a single PID, every per-PID metric equals the global total."""
        streaming_stats.update(12345, incremental_gc_stats_item)

        pid_stats = streaming_stats.get_pid_stats(12345)
        assert pid_stats is not None
        for metric_key, gen_stats in streaming_stats.metrics.items():
            for gen, total in gen_stats.items():
                assert pid_stats[metric_key][gen].count() == total.count(), metric_key
                assert pid_stats[metric_key][gen].sum() == total.sum(), metric_key


class TestStreamingStatsPidEviction:
    """Tests for StreamingStats PID eviction."""

    def test_eviction_materializes_old_pid(
        self,
        streaming_stats: StreamingStats,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        for pid in range(StreamingStats.MAX_ACTIVE_PIDS + 1):
            streaming_stats.update(pid, gc_stats_item_factory())

        old_pid = 0
        assert old_pid not in streaming_stats._metrics_per_pid
        assert old_pid in streaming_stats._materialized_metrics

    def test_eviction_respects_max_active_pids(
        self,
        streaming_stats: StreamingStats,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        for pid in range(StreamingStats.MAX_ACTIVE_PIDS + 10):
            streaming_stats.update(pid, gc_stats_item_factory())

        assert len(streaming_stats._metrics_per_pid) == StreamingStats.MAX_ACTIVE_PIDS

    def test_eviction_fifo_order(
        self,
        streaming_stats: StreamingStats,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        for pid in range(StreamingStats.MAX_ACTIVE_PIDS + 1):
            streaming_stats.update(pid, gc_stats_item_factory())

        assert 0 not in streaming_stats._metrics_per_pid
        assert StreamingStats.MAX_ACTIVE_PIDS in streaming_stats._metrics_per_pid


class TestStreamingStatsReadTime:
    """Tests for StreamingStats.record_read_time and the read_time property."""

    def test_read_time_empty_by_default(self, streaming_stats: StreamingStats) -> None:
        assert streaming_stats.read_time.count() == 0
        assert streaming_stats.read_time.sum() == 0
        assert streaming_stats.read_time.average() == 0.0

    def test_record_read_time_accumulates(self, streaming_stats: StreamingStats) -> None:
        for duration_ns in (100_000, 200_000, 300_000):
            streaming_stats.record_read_time(duration_ns)

        assert streaming_stats.read_time.count() == 3
        assert streaming_stats.read_time.sum() == 600_000
        assert streaming_stats.read_time.average() == 200_000

    def test_record_read_time_stores_nanoseconds_exactly(self, streaming_stats: StreamingStats) -> None:
        streaming_stats.record_read_time(1_500)
        streaming_stats.record_read_time(501)

        assert streaming_stats.read_time.sum() == 2_001

    def test_record_read_time_percentiles(self, streaming_stats: StreamingStats) -> None:
        for value in range(1, 101):
            streaming_stats.record_read_time(value)

        assert streaming_stats.read_time.percentile(50) == 50.5
        assert abs(streaming_stats.read_time.percentile(99) - 99.01) < 1e-9
        assert streaming_stats.read_time.percentile(100) == 100.0

    def test_record_read_time_zero(self, streaming_stats: StreamingStats) -> None:
        streaming_stats.record_read_time(0)

        assert streaming_stats.read_time.count() == 1
        assert streaming_stats.read_time.sum() == 0

    def test_read_time_independent_of_pause_metrics(
        self,
        streaming_stats: StreamingStats,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        streaming_stats.update(12345, gc_stats_item_factory(ts_start=0, ts_stop=1_000_000))
        streaming_stats.record_read_time(42_000)

        assert streaming_stats.count() == 1
        assert streaming_stats.read_time.count() == 1
        assert streaming_stats.read_time.sum() == 42_000
        assert streaming_stats.metrics["pause"][0].sum() == 1_000_000

    def test_read_time_excluded_from_aggregate(self, streaming_stats: StreamingStats) -> None:
        streaming_stats.record_read_time(1_000_000)

        assert streaming_stats.aggregate() == {"pause_count": 0}


class TestStreamingStatsAggregate:
    """Tests for StreamingStats.aggregate method."""

    def test_aggregate_pause_metrics(self, streaming_stats_with_pids: StreamingStats) -> None:
        result = streaming_stats_with_pids.aggregate()
        for gen in range(3):
            assert f"pause_gen_{gen}_p99" in result
            assert f"pause_gen_{gen}_sum" in result
            assert f"pause_gen_{gen}_count" in result

    def test_aggregate_heap_size_p99(self, streaming_stats_with_pids: StreamingStats) -> None:
        result = streaming_stats_with_pids.aggregate()
        assert "heap_size_p99" in result

    def test_aggregate_pause_count(self, streaming_stats_with_pids: StreamingStats) -> None:
        result = streaming_stats_with_pids.aggregate()
        assert result["pause_count"] == streaming_stats_with_pids.count()

    def test_aggregate_empty(self, streaming_stats: StreamingStats) -> None:
        result = streaming_stats.aggregate()
        assert result == {"pause_count": 0}

    def test_aggregate_values_in_milliseconds(
        self,
        streaming_stats: StreamingStats,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        item = gc_stats_item_factory(ts_start=0, ts_stop=1_000_000, heap_size=1_000_000)
        streaming_stats.update(12345, item)
        result = streaming_stats.aggregate()
        assert result["pause_gen_0_sum"] == 1.0

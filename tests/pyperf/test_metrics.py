"""Tests for the flat metric names a benchmark reads a run through.

These moved here with the projection. The keys are pyperf's contract, and
`StreamingStats` has no reason to know them.
"""

from __future__ import annotations

import pytest

from gcmon.pyperf.metrics import to_metrics
from gcmon.stats.stats import StreamingStats
from tests.helpers import create_mock_stats_item


def _stats_with_pids() -> StreamingStats:
    """Three pids, three generations each, with a heap size that varies."""
    ss = StreamingStats()
    for pid in (11111, 22222, 33333):
        for gen in range(3):
            ss.update(
                pid,
                create_mock_stats_item(
                    gen=gen,
                    ts_start=1_000_000_000,
                    ts_stop=1_005_000_000 + gen * 1_000_000,
                    heap_size=1_000_000 * (gen + 1),
                ),
            )
    return ss


class TestMetricKeys:
    def test_every_generation_reports_its_pause_metrics(self) -> None:
        result = to_metrics(_stats_with_pids())

        for gen in range(3):
            assert f"pause_gen_{gen}_p99" in result
            assert f"pause_gen_{gen}_sum" in result
            assert f"pause_gen_{gen}_count" in result

    def test_heap_size_is_reported(self) -> None:
        assert "heap_size_p99" in to_metrics(_stats_with_pids())

    def test_pause_count_matches_what_was_recorded(self) -> None:
        stats = _stats_with_pids()

        assert to_metrics(stats)["pause_count"] == stats.count()

    def test_an_empty_run_reports_only_its_zero(self) -> None:
        assert to_metrics(StreamingStats()) == {"pause_count": 0}

    def test_read_time_is_not_a_metric(self) -> None:
        """Read durations are gcmon's own overhead, not the target's."""
        stats = StreamingStats()
        stats.record_read_time(1_000_000)

        assert to_metrics(stats) == {"pause_count": 0}

    def test_durations_are_milliseconds(self) -> None:
        stats = StreamingStats()
        stats.update(12345, create_mock_stats_item(ts_start=0, ts_stop=1_000_000, heap_size=1_000_000))

        assert to_metrics(stats)["pause_gen_0_sum"] == 1.0


class TestMetricsAreExact:
    def test_sums_and_counts_are_exact(self) -> None:
        stats = StreamingStats()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        stats.record_loss(1, 0, 0, 9, 9_000_000)

        result = to_metrics(stats)

        assert result["pause_gen_0_count"] == 10
        assert result["pause_gen_0_sum"] == pytest.approx(10.0)
        assert result["pause_count"] == 10

    def test_coverage_is_reported(self) -> None:
        stats = StreamingStats()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000))
        stats.record_loss(1, 0, 0, 1, 1_000)

        assert to_metrics(stats)["pause_gen_0_coverage"] == pytest.approx(0.5)

    def test_they_fold_every_ring_of_the_run(self) -> None:
        """Run-wide by design, which is the scope these key names were
        released with: a per-ring key would embed a pid that differs every
        run, and no two runs would share one."""
        stats = StreamingStats()
        stats.update(1, create_mock_stats_item(iid=0, gen=0, ts_start=0, ts_stop=1_000_000))
        stats.update(1, create_mock_stats_item(iid=1, gen=0, ts_start=0, ts_stop=1_000_000))
        stats.record_loss(1, 1, 0, 2, 2_000_000)

        result = to_metrics(stats)

        assert result["pause_gen_0_count"] == 4
        assert result["pause_gen_0_sum"] == pytest.approx(4.0)
        assert result["pause_gen_0_coverage"] == pytest.approx(0.5)

    def test_lifetime_metrics_appear_only_when_recorded(self) -> None:
        stats = StreamingStats()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000))

        assert "pause_gen_0_lifetime_count" not in to_metrics(stats)

        stats.observe_cumulative(1, 0, 0, 5_000, 5.0)

        assert to_metrics(stats)["pause_gen_0_lifetime_count"] == 5_000

    def test_p99_stays_sampled(self) -> None:
        """No scale factor corrects a quantile; see ADR-0015."""
        stats = StreamingStats()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000_000))
        without = to_metrics(stats)["pause_gen_0_p99"]
        stats.record_loss(1, 0, 0, 99, 99_000_000)

        assert to_metrics(stats)["pause_gen_0_p99"] == without

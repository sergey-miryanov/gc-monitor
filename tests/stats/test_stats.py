"""Tests for Stats class."""

from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest

from gcmon.stats import HAS_DDSKETCH, Stats, StreamingStats
from tests.helpers import create_mock_stats_item


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

    def test_update_after_materialize_keeps_the_count_and_the_sum(self, stats_with_data: Stats) -> None:
        """A ring evicted and seen again resumes this instance, so the two
        stretches read as one in `Count` and `Sum`."""
        stats_with_data.materialize()
        stats_with_data.update(600.0)

        assert stats_with_data.count() == 6
        assert stats_with_data.sum() == 2_100.0

    def test_update_after_materialize_restarts_the_percentiles(self, stats_with_data: Stats) -> None:
        """The settled ones describe the stretch before the eviction, which is
        not the one the counts beside them now cover."""
        stats_with_data.materialize()
        settled = stats_with_data.percentiles
        assert settled is not None and settled[50] == 300.0

        stats_with_data.update(600.0)

        assert stats_with_data.percentile(50) == 600.0

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


class TestExactTotals:
    """Loss arrives per poll, so the exact totals follow from ADR-0015's
    invariant: what gcmon saw plus what the target's counters say it missed."""

    def _stats(self, sampled: int = 3, lost: int = 7) -> StreamingStats:
        stats = StreamingStats()
        for _ in range(sampled):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000))
        stats.record_loss(1, 0, 0, lost, lost * 1_000)
        return stats

    def test_exact_is_sampled_plus_lost(self) -> None:
        stats = self._stats()

        assert stats.pause_totals(1, 0, 0).exact_count == 10
        assert stats.pause_totals(1, 0, 0).exact_pause_ns == 10_000

    def test_coverage_and_scale_agree_with_the_totals(self) -> None:
        stats = self._stats()

        assert stats.pause_totals(1, 0, 0).coverage == pytest.approx(0.3)
        assert stats.pause_totals(1, 0, 0).scale_factor == pytest.approx(10 / 3)

    def test_an_untouched_generation_is_neutral(self) -> None:
        """1.0 rather than a division by zero, so no call site has to guard."""
        stats = StreamingStats()

        assert stats.pause_totals(1, 0, 2).coverage == 1.0
        assert stats.pause_totals(1, 0, 2).scale_factor == 1.0
        assert stats.pause_totals(1, 0, 2).exact_count == 0

    def test_a_lossless_run_reports_full_coverage(self) -> None:
        stats = StreamingStats()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000))

        assert stats.pause_totals(1, 0, 0).coverage == 1.0
        assert stats.pause_totals(1, 0, 0).exact_count == 1

    def test_totals_span_every_pid(self) -> None:
        stats = self._stats()
        stats.update(2, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000))
        stats.record_loss(2, 0, 0, 1, 1_000)

        assert stats.pause_totals_by_gen()[0].exact_count == 12
        assert stats.pause_totals(2, 0, 0).exact_count == 2

    def test_loss_survives_a_pid_the_monitor_forgets(self) -> None:
        """Recorded per poll rather than flushed at the end, so a child that
        exits mid-run still counts."""
        stats = self._stats()
        before = stats.pause_totals_by_gen()[0].exact_count

        assert before == stats.pause_totals_by_gen()[0].exact_count
        assert stats.pause_totals(1, 0, 0).lost_count == 7


class TestTotalsLeaveTheAccumulatorBehind:
    """`StreamingStats` keeps its accumulators and answers from copies of
    them, so a caller writing to what it got back changes nothing."""

    def test_one_pid_gets_an_answer_rather_than_the_slot(self) -> None:
        stats = StreamingStats()
        stats.record_loss(1, 0, 0, 7, 7_000)

        totals = stats.pause_totals(1, 0, 0)
        with pytest.raises(AttributeError):
            totals.lost_count = 99  # type: ignore[misc]

        assert (totals.lost_count, totals.lost_pause_ns) == (7, 7_000)
        assert stats.pause_totals(1, 0, 0).lost_count == 7

    def test_every_pid_gets_one_too(self) -> None:
        stats = StreamingStats()
        stats.record_loss(1, 0, 0, 7, 7_000)
        stats.record_loss(2, 0, 0, 1, 1_000)

        with pytest.raises(AttributeError):
            stats.pause_totals_by_gen()[0].lost_count = 99  # type: ignore[misc]

        assert stats.pause_totals_by_gen()[0].lost_count == 8

    def test_an_untouched_key_answers_zero(self) -> None:
        stats = StreamingStats()

        assert stats.pause_totals(1, 0, 0).lost_count == 0
        assert stats.pause_totals_by_gen()[2].lost_pause_ns == 0

    def test_polls_still_accumulate(self) -> None:
        stats = StreamingStats()
        stats.record_loss(1, 0, 0, 3, 3_000)
        stats.record_loss(1, 0, 0, 4, 4_000)

        assert stats.pause_totals(1, 0, 0).lost_count == 7
        assert stats.pause_totals(1, 0, 0).lost_pause_ns == 7_000

    def test_lifetime_reads_hand_back_scratch(self) -> None:
        """`LifetimeTotals` is the accumulator a fold adds into, so this side
        cannot be frozen the way the pause side is. The fold still sums into
        a fresh one, which is what the write below lands on."""
        stats = StreamingStats()
        stats.record_lifetime(1, 0, 0, 40, 4.0)
        stats.record_lifetime(1, 1, 0, 2, 0.5)

        stats.lifetime_totals_by_gen()[0].add(99, 9.0)

        assert stats.lifetime_totals_by_gen()[0].collections == 42
        assert stats.lifetime_totals_by_gen()[0].pause_ns == 4_500_000_000


class TestLowCoverage:
    """Which ring gcmon read too little of, and how little.

    The answer is three numbers, so nothing here reads a log: wording it and
    saying it once belong to the monitor, in `test_monitor_coverage.py`.
    """

    def _sampled(self, stats: StreamingStats, count: int, iid: int = 0) -> None:
        for _ in range(count):
            stats.update(1, create_mock_stats_item(iid=iid, gen=0, ts_start=0, ts_stop=1_000))

    def test_it_names_the_ring_and_its_coverage(self) -> None:
        stats = StreamingStats()
        self._sampled(stats, 3)

        stats.record_loss(1, 0, 0, 7, 7_000)

        low = stats.low_coverage(1)
        assert low is not None
        iid, gen, coverage = low
        assert (iid, gen) == (0, 0)
        assert coverage == pytest.approx(0.3)

    def test_a_starved_interpreter_answers_beside_a_covered_one(self) -> None:
        """The blended figure clears the floor, which is what kept this quiet
        while the key was per process."""
        stats = StreamingStats()
        self._sampled(stats, 99, iid=0)
        self._sampled(stats, 2, iid=1)
        stats.record_loss(1, 1, 0, 8, 8_000)

        assert stats.pause_totals_by_gen()[0].coverage > StreamingStats.COVERAGE_ADVISORY

        low = stats.low_coverage(1)
        assert low is not None
        iid, gen, coverage = low
        assert (iid, gen) == (1, 0)
        assert coverage == pytest.approx(0.2)

    def test_it_answers_with_the_worst_ring_rather_than_the_first(self) -> None:
        """The caller says it once, so a marginal figure must not stand in for
        a capture holding an interpreter at a tenth of its collections."""
        stats = StreamingStats()
        self._sampled(stats, 87, iid=0)
        stats.record_loss(1, 0, 0, 13, 13_000)
        self._sampled(stats, 1, iid=1)
        stats.record_loss(1, 1, 0, 19, 19_000)

        low = stats.low_coverage(1)
        assert low is not None
        iid, gen, coverage = low
        assert (iid, gen) == (1, 0), "interpreter 0 dipped first and is the milder of the two"
        assert coverage == pytest.approx(0.05)

    def test_the_worst_ring_wins_whichever_order_the_loss_arrived_in(self) -> None:
        stats = StreamingStats()
        self._sampled(stats, 1, iid=1)
        stats.record_loss(1, 1, 0, 19, 19_000)
        self._sampled(stats, 87, iid=0)
        stats.record_loss(1, 0, 0, 13, 13_000)

        low = stats.low_coverage(1)
        assert low is not None
        assert low[:2] == (1, 0)

    def test_a_covered_interpreter_does_not_answer_for_a_starved_one(self) -> None:
        stats = StreamingStats()
        self._sampled(stats, 99, iid=0)
        self._sampled(stats, 2, iid=1)
        stats.record_loss(1, 0, 0, 1, 1_000)

        assert stats.low_coverage(1) is None

    def test_a_covered_run_answers_nothing(self) -> None:
        stats = StreamingStats()
        self._sampled(stats, 99)

        stats.record_loss(1, 0, 0, 1, 1_000)

        assert stats.pause_totals(1, 0, 0).coverage > StreamingStats.COVERAGE_ADVISORY
        assert stats.low_coverage(1) is None

    def test_a_run_that_lost_nothing_answers_nothing(self) -> None:
        """The shortcut the check leads with: a ring that lost nothing is
        fully covered, whatever its sample size."""
        stats = StreamingStats()
        self._sampled(stats, 3)

        assert stats.low_coverage(1) is None

    def test_one_pids_loss_does_not_answer_for_another(self) -> None:
        stats = StreamingStats()
        self._sampled(stats, 3)

        stats.record_loss(2, 0, 0, 7, 7_000)

        assert stats.low_coverage(1) is None
        assert stats.low_coverage(2) == (0, 0, 0.0), "pid 2 sampled nothing of what it lost"

    def test_asking_twice_answers_twice(self) -> None:
        """No latch of its own: every poll asks, and a second reader must not
        be told a blind run is healthy because the first one asked first."""
        stats = StreamingStats()
        self._sampled(stats, 3)
        stats.record_loss(1, 0, 0, 7, 7_000)

        first = stats.low_coverage(1)
        assert first is not None
        assert stats.low_coverage(1) == first


class TestLifetimeTotals:
    def test_summed_across_interpreters(self) -> None:
        stats = StreamingStats()
        stats.record_lifetime(1, 0, 0, 500, 0.5)
        stats.record_lifetime(1, 1, 0, 300, 0.3)

        assert stats.lifetime_totals_by_gen()[0].collections == 800
        assert stats.lifetime_totals_by_gen()[0].pause_ns == 800_000_000

    def test_the_newest_value_replaces_the_last(self) -> None:
        """Cumulative in the target, so polls report a running total, not a
        delta -- adding them would count every collection many times."""
        stats = StreamingStats()
        stats.record_lifetime(1, 0, 0, 500, 0.5)
        stats.record_lifetime(1, 0, 0, 900, 0.9)

        assert stats.lifetime_totals_by_gen()[0].collections == 900

    def test_it_can_exceed_the_observed_span(self) -> None:
        """The point of reporting it: what ran before gcmon attached is not
        loss, and must not touch `Cov`."""
        stats = StreamingStats()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000))
        stats.record_lifetime(1, 0, 0, 5_000, 5.0)

        assert stats.lifetime_totals_by_gen()[0].collections == 5_000
        assert stats.pause_totals(1, 0, 0).exact_count == 1
        assert stats.pause_totals(1, 0, 0).coverage == 1.0


class TestTwoInterpretersOfOnePid:
    """The arithmetic the old key could not carry.

    Every statistics test drove one interpreter, and one interpreter cannot
    supply an input a per-process figure and a per-ring one disagree on.
    """

    def _stats(self) -> StreamingStats:
        """Nine records read of ten on iid 0, one of ten on iid 1. Blended,
        the pid reads 50%."""
        stats = StreamingStats()
        for _ in range(9):
            stats.update(1, create_mock_stats_item(iid=0, gen=0, ts_start=0, ts_stop=1_000))
        stats.record_loss(1, 0, 0, 1, 1_000)

        stats.update(1, create_mock_stats_item(iid=1, gen=0, ts_start=0, ts_stop=5_000))
        stats.record_loss(1, 1, 0, 9, 45_000)
        return stats

    def test_each_interpreter_reports_its_own_coverage(self) -> None:
        stats = self._stats()

        assert stats.pause_totals(1, 0, 0).coverage == pytest.approx(0.9)
        assert stats.pause_totals(1, 1, 0).coverage == pytest.approx(0.1)

    def test_the_sampled_durations_stay_apart(self) -> None:
        stats = self._stats()

        assert stats.pause_totals(1, 0, 0).sampled_pause_ns == 9_000
        assert stats.pause_totals(1, 1, 0).sampled_pause_ns == 5_000

    def test_the_run_still_folds_to_one_answer(self) -> None:
        """Rings are what the key separates; a roll-up over all of them is
        still one number, and it is the one `Total` prints."""
        stats = self._stats()
        totals = stats.pause_totals_by_gen()[0]

        assert (totals.sampled_count, totals.lost_count) == (10, 10)
        assert totals.coverage == pytest.approx(0.5)

    def test_the_two_rings_are_two_entries(self) -> None:
        stats = self._stats()

        assert stats.rings() == {(1, 0), (1, 1)}


class TestAnEvictionRoundTrip:
    """A ring seen again after eviction resumes the entry it left behind.

    Starting it blank shadowed the materialized one, which then never printed
    and let `Total` exceed the rows beneath it.
    """

    def _evicted(self) -> StreamingStats:
        """Ring (1, 0) with three records, pushed out by a full active set."""
        stats = StreamingStats()
        for _ in range(3):
            stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000))
        for pid in range(2, StreamingStats.MAX_ACTIVE_RINGS + 2):
            stats.update(pid, create_mock_stats_item(gen=0, ts_start=0, ts_stop=1_000))
        return stats

    def test_the_ring_leaves_the_active_set(self) -> None:
        stats = self._evicted()

        assert (1, 0) not in stats._metrics_per_ring
        assert (1, 0) in stats._materialized_metrics

    def test_count_and_sum_cover_both_stretches(self) -> None:
        stats = self._evicted()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=4_000))
        totals = stats.pause_totals(1, 0, 0)

        assert totals.sampled_count == 4
        assert totals.sampled_pause_ns == 7_000

    def test_a_resumed_ring_lives_in_one_dict(self) -> None:
        stats = self._evicted()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=4_000))

        assert (1, 0) in stats._metrics_per_ring
        assert (1, 0) not in stats._materialized_metrics

    def test_resuming_holds_the_bound(self) -> None:
        stats = self._evicted()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=4_000))

        assert len(stats._metrics_per_ring) == StreamingStats.MAX_ACTIVE_RINGS

    def test_total_equals_the_sum_of_the_rings(self) -> None:
        stats = self._evicted()
        stats.update(1, create_mock_stats_item(gen=0, ts_start=0, ts_stop=4_000))

        rings = sum(stats.pause_totals(pid, iid, 0).sampled_count for pid, iid in stats.rings())

        assert rings == stats.pause_totals_by_gen()[0].sampled_count

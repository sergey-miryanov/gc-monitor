"""Tests for metric classes."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from gc_monitor.data import GCStatsInfo, IncrementalGCStatsInfo
from gc_monitor.stats import (
    INCREMENTAL_METRICS,
    METRICS,
    DeduceUnreachableMetric,
    FillIncrementMetric,
    MarkAliveMetric,
    PauseMetric,
)


class TestPauseMetric:
    """Tests for PauseMetric class."""

    def test_name(self) -> None:
        metric = PauseMetric()
        assert metric.name == "GC Pause"

    def test_get_values(
        self,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        metric = PauseMetric()
        item = gc_stats_item_factory(ts_start=1000, ts_stop=5000)
        ts_start, ts_stop = metric.get_values(item)
        assert ts_start == 1000
        assert ts_stop == 5000


class TestMarkAliveMetric:
    """Tests for MarkAliveMetric class."""

    def test_name(self) -> None:
        metric = MarkAliveMetric()
        assert metric.name == "GC Mark Alive"

    def test_get_values(
        self,
        incremental_gc_stats_item_factory: Callable[..., IncrementalGCStatsInfo],
    ) -> None:
        metric = MarkAliveMetric()
        item = incremental_gc_stats_item_factory(
            ts_mark_alive_start=2000, ts_mark_alive_stop=4000,
        )
        ts_start, ts_stop = metric.get_values(item)
        assert ts_start == 2000
        assert ts_stop == 4000

    def test_get_values_asserts_non_incremental(
        self,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        metric = MarkAliveMetric()
        item = gc_stats_item_factory()
        ts1, ts2 = metric.get_values(item)
        assert(ts1 == 0)
        assert(ts2 == 0)


class TestFillIncrementMetric:
    """Tests for FillIncrementMetric class."""

    def test_name(self) -> None:
        metric = FillIncrementMetric()
        assert metric.name == "GC Fill Increment"

    def test_get_values(
        self,
        incremental_gc_stats_item_factory: Callable[..., IncrementalGCStatsInfo],
    ) -> None:
        metric = FillIncrementMetric()
        item = incremental_gc_stats_item_factory(
            ts_fill_increment_start=3000, ts_fill_increment_stop=5000,
        )
        ts_start, ts_stop = metric.get_values(item)
        assert ts_start == 3000
        assert ts_stop == 5000

    def test_get_values_asserts_non_incremental(
        self,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        metric = FillIncrementMetric()
        item = gc_stats_item_factory()
        ts1, ts2 = metric.get_values(item)
        assert(ts1 == 0)
        assert(ts2 == 0)


class TestDeduceUnreachableMetric:
    """Tests for DeduceUnreachableMetric class."""

    def test_name(self) -> None:
        metric = DeduceUnreachableMetric()
        assert metric.name == "GC Deduce Unreachable"

    def test_get_values(
        self,
        incremental_gc_stats_item_factory: Callable[..., IncrementalGCStatsInfo],
    ) -> None:
        metric = DeduceUnreachableMetric()
        item = incremental_gc_stats_item_factory(
            ts_deduce_unreachable_start=7000, ts_deduce_unreachable_stop=9000,
        )
        ts_start, ts_stop = metric.get_values(item)
        assert ts_start == 7000
        assert ts_stop == 9000

    def test_get_values_asserts_non_incremental(
        self,
        gc_stats_item_factory: Callable[..., GCStatsInfo],
    ) -> None:
        metric = DeduceUnreachableMetric()
        item = gc_stats_item_factory()
        ts1, ts2 = metric.get_values(item)
        assert(ts1 == 0)
        assert(ts2 == 0)


class TestMetricDictionaries:
    """Tests for METRICS and INCREMENTAL_METRICS dictionaries."""

    def test_metrics_contains_pause(self) -> None:
        assert "pause" in METRICS
        assert isinstance(METRICS["pause"], PauseMetric)

    def test_metrics_contains_all_incremental(self) -> None:
        for key in INCREMENTAL_METRICS:
            assert key in METRICS

    def test_incremental_metrics_keys(self) -> None:
        assert "mark_alive" in INCREMENTAL_METRICS
        assert "fill_increment" in INCREMENTAL_METRICS
        assert "deduce_unreachable" in INCREMENTAL_METRICS

    def test_metrics_instances(self) -> None:
        assert isinstance(METRICS["pause"], PauseMetric)
        assert isinstance(METRICS["mark_alive"], MarkAliveMetric)
        assert isinstance(METRICS["fill_increment"], FillIncrementMetric)
        assert isinstance(METRICS["deduce_unreachable"], DeduceUnreachableMetric)

    def test_metrics_count(self) -> None:
        assert len(METRICS) == 1 + len(INCREMENTAL_METRICS)

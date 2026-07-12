"""Benchmarks for the streaming statistics hot path.

gcmon feeds every GC event through StreamingStats to keep running percentiles
and per-pid aggregates. These benchmarks cover the ingest loop, quantile
computation, and the final aggregation step.
"""

from __future__ import annotations

import pytest
from pytest_codspeed import BenchmarkFixture

from gcmon.stats import Stats, StreamingStats, get_quantile_value

from .conftest import make_gc_event

EVENT_COUNT = 5_000


@pytest.mark.benchmark
def test_streaming_stats_update_single_pid(benchmark: BenchmarkFixture) -> None:
    events = [make_gc_event(i) for i in range(EVENT_COUNT)]

    def run() -> StreamingStats:
        stats = StreamingStats()
        for event in events:
            stats.update(12345, event)
        return stats

    result = benchmark(run)
    assert result.count() == EVENT_COUNT


@pytest.mark.benchmark
def test_streaming_stats_update_many_pids(benchmark: BenchmarkFixture) -> None:
    # Spread events across more pids than MAX_ACTIVE_PIDS to exercise the
    # eviction + materialize path.
    events = [(1000 + (i % 128), make_gc_event(i, pid=1000 + (i % 128))) for i in range(EVENT_COUNT)]

    def run() -> StreamingStats:
        stats = StreamingStats()
        for pid, event in events:
            stats.update(pid, event)
        return stats

    result = benchmark(run)
    assert result.count() == EVENT_COUNT


@pytest.mark.benchmark
def test_streaming_stats_aggregate(benchmark: BenchmarkFixture) -> None:
    stats = StreamingStats()
    for i in range(EVENT_COUNT):
        stats.update(12345, make_gc_event(i, gen=i % 3))

    result = benchmark(stats.aggregate)
    assert result["pause_count"] == EVENT_COUNT


@pytest.mark.benchmark
def test_stats_update_and_percentiles(benchmark: BenchmarkFixture) -> None:
    values = [float((i * 7919) % 100_003) for i in range(EVENT_COUNT)]

    def run() -> tuple[float, float]:
        stats = Stats()
        for value in values:
            stats.update(value)
        return stats.percentile(50), stats.percentile(99)

    p50, p99 = benchmark(run)
    assert p99 >= p50


@pytest.mark.benchmark
def test_get_quantile_value(benchmark: BenchmarkFixture) -> None:
    buffer = sorted(float((i * 7919) % 100_003) for i in range(EVENT_COUNT))

    def run() -> float:
        total = 0.0
        for q in (50, 90, 95, 99):
            total += get_quantile_value(buffer, q)
        return total

    assert benchmark(run) > 0

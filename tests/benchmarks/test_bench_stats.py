"""Benchmarks for the streaming statistics hot path.

gcmon feeds every GC event through StreamingStats to keep running percentiles
and per-ring aggregates. These benchmarks cover the ingest loop, quantile
computation, and the final aggregation step.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_codspeed import BenchmarkFixture

from gcmon.pyperf.metrics import to_metrics
from gcmon.stats import Stats, StreamingStats, get_quantile_value

from .conftest import make_gc_event

EVENT_COUNT = 5_000
# A process tree wide enough for the settling cost to be visible, with several
# interpreters each, since settling walks the rings rather than the pids.
FAN_OUT_PIDS = 1_000
FAN_OUT_IIDS = 3
FAN_OUT_FIRST_PID = 1_000


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
    # Spread events across more pids than MAX_ACTIVE_RINGS, so the run fills
    # the bound and then declines. The name stays so CodSpeed keeps one series
    # across the bound's move from processes to rings.
    events = [(1000 + (i % 200), make_gc_event(i, pid=1000 + (i % 200))) for i in range(EVENT_COUNT)]

    def run() -> StreamingStats:
        stats = StreamingStats()
        for pid, event in events:
            stats.update(pid, event)
        return stats

    result = benchmark(run)
    assert result.count() == EVENT_COUNT


@pytest.mark.benchmark
def test_streaming_stats_retain_wide_fan_out(benchmark: BenchmarkFixture) -> None:
    """A whole fan-out exiting inside one tick.

    The monitor settles departed pids before it polls, so this sits in the
    poll interval every surviving ring is filling against. The fan-out is
    built in `setup`, which runs once per round and is not measured: `retain`
    settles a pid once, so a second call over the same state would measure an
    empty set difference.
    """

    def setup() -> tuple[tuple[Any, ...], dict[str, Any]]:
        stats = StreamingStats()
        for pid in range(FAN_OUT_FIRST_PID, FAN_OUT_FIRST_PID + FAN_OUT_PIDS):
            for iid in range(FAN_OUT_IIDS):
                stats.update(pid, make_gc_event(pid + iid, pid=pid, iid=iid))
        return (stats,), {}

    def run(stats: StreamingStats) -> StreamingStats:
        stats.retain(set())
        return stats

    result = benchmark.pedantic(run, setup=setup)
    # Not timing: a run that stopped settling would otherwise read as a win.
    assert result._running_rings == {}
    assert len(result.rings()) == StreamingStats.MAX_ACTIVE_RINGS


@pytest.mark.benchmark
def test_streaming_stats_aggregate(benchmark: BenchmarkFixture) -> None:
    """The projection, which used to be `StreamingStats.aggregate`. The name
    stays so CodSpeed keeps one series across the move, and *stats* goes
    through the fixture so the number leaves out a wrapper frame."""
    stats = StreamingStats()
    for i in range(EVENT_COUNT):
        stats.update(12345, make_gc_event(i, gen=i % 3))

    result = benchmark(to_metrics, stats)
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

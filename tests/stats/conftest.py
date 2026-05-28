"""Shared pytest fixtures for stats module tests."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest

from gc_monitor.data import GCStatsInfo, IncrementalGCStatsInfo
from gc_monitor.stats import Stats, StreamingStats


@pytest.fixture
def stats() -> Stats:
    """Create a fresh Stats instance."""
    return Stats()


@pytest.fixture
def stats_with_data() -> Stats:
    """Create a Stats instance with pre-populated data."""
    s = Stats()
    for v in [100.0, 200.0, 300.0, 400.0, 500.0]:
        s.update(v)
    return s


@pytest.fixture
def streaming_stats() -> StreamingStats:
    """Create a fresh StreamingStats instance."""
    return StreamingStats()


@pytest.fixture
def streaming_stats_with_pids(
    gc_stats_item_factory: Callable[..., GCStatsInfo],
) -> StreamingStats:
    """Create a StreamingStats instance with multiple PIDs."""
    ss = StreamingStats()
    for pid in (11111, 22222, 33333):
        for gen in range(3):
            item = gc_stats_item_factory(
                gen=gen,
                ts_start=1_000_000_000,
                ts_stop=1_005_000_000 + gen * 1_000_000,
                heap_size=1_000_000 * (gen + 1),
            )
            ss.update(pid, item)
    return ss


@pytest.fixture
def gc_stats_item_factory() -> Callable[..., GCStatsInfo]:
    """Factory fixture that creates GCStatsInfo with overridable defaults.

    Usage:
        item = gc_stats_item_factory(heap_size=1_000_000, ts_stop=5000)
    """

    def _factory(**kwargs: object) -> GCStatsInfo:
        defaults: dict[str, object] = dict(
            gen=0, iid=0, ts_start=0, ts_stop=1000,
            heap_size=0, collections=0, collected=0, uncollectable=0,
            candidates=0, duration=0.0,
        )
        defaults.update(kwargs)
        return GCStatsInfo(**defaults)  # type: ignore[arg-type]

    return _factory


@pytest.fixture
def incremental_gc_stats_item_factory() -> Callable[..., IncrementalGCStatsInfo]:
    """Factory fixture that creates IncrementalGCStatsInfo with overridable defaults.

    Usage:
        item = incremental_gc_stats_item_factory(ts_mark_alive_stop=5000)
    """

    def _factory(**kwargs: object) -> IncrementalGCStatsInfo:
        defaults: dict[str, object] = dict(
            gen=0, iid=0, ts_start=0, ts_stop=10000,
            heap_size=0, collections=0, collected=0, uncollectable=0,
            candidates=0, duration=0.0,
            increment_size=0, alive_size=0,
            ts_mark_alive_start=0, ts_mark_alive_stop=2000,
            ts_fill_increment_start=2000, ts_fill_increment_stop=5000,
            ts_deduce_unreachable_start=5000, ts_deduce_unreachable_stop=10000,
        )
        defaults.update(kwargs)
        return IncrementalGCStatsInfo(**defaults)  # type: ignore[arg-type]

    return _factory


@pytest.fixture
def incremental_gc_stats_item(
    incremental_gc_stats_item_factory: Callable[..., IncrementalGCStatsInfo],
) -> IncrementalGCStatsInfo:
    """Create a ready-made IncrementalGCStatsInfo instance."""
    return incremental_gc_stats_item_factory()


@pytest.fixture
def stats_without_ddsketch() -> Stats:
    """Create a Stats instance with DDSketch disabled."""
    with patch("gc_monitor.stats.HAS_DDSKETCH", False):
        s = Stats()
        assert s._sketch is None
        return s

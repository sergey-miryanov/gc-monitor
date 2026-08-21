"""Shared pytest fixtures for stats module tests."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest

from gcmon.model.data import GCStatsInfo
from gcmon.stats.stats import Stats
from gcmon.stats.streaming_stats import StreamingStats
from tests.helpers import create_mock_incremental_item, create_mock_stats_item


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
    return create_mock_stats_item


@pytest.fixture
def incremental_gc_stats_item_factory() -> Callable[..., GCStatsInfo]:
    """Factory fixture that creates GCStatsInfo (with optional incremental fields)."""
    return create_mock_incremental_item


@pytest.fixture
def incremental_gc_stats_item(
    incremental_gc_stats_item_factory: Callable[..., GCStatsInfo],
) -> GCStatsInfo:
    """Create a ready-made GCStatsInfo with incremental fields."""
    return incremental_gc_stats_item_factory()


@pytest.fixture
def stats_without_ddsketch() -> Stats:
    """Create a Stats instance with DDSketch disabled."""
    with patch("gcmon.stats.stats.HAS_DDSKETCH", False):
        s = Stats()
        assert not s.has_sketch
        return s

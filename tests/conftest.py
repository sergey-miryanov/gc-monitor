"""Shared pytest fixtures for gc-monitor tests."""

from __future__ import annotations

import logging
import subprocess
from unittest.mock import Mock

import pytest

from tests.helpers import create_mock_stats_item


@pytest.fixture
def mock_stats_item() -> Mock:
    """Create a mock StatsItem with default values.

    Note: ts is in nanoseconds (int), duration and total_duration are in seconds (float).

    Returns:
        Mock StatsItem instance with all required fields.
    """
    return create_mock_stats_item(gen=2)


@pytest.fixture
def mock_stats_item_batch() -> list[Mock]:
    """Create a batch of mock StatsItem instances with incrementing values.

    Returns:
        List of 3 mock StatsItem instances with different generations.
    """
    items: list[Mock] = []
    for gen in range(3):
        item = create_mock_stats_item(
            gen=gen,
            ts=1_000_000_000 + gen * 100_000_000,
            collections=10 * (gen + 1),
            collected=50 * (gen + 1),
            uncollectable=gen,
            candidates=20 * (gen + 1),
            object_visits=100 * (gen + 1),
            objects_transitively_reachable=50 * (gen + 1),
            objects_not_transitively_reachable=30 * (gen + 1),
            heap_size=1_000_000 * (gen + 1),
            work_to_do=5 * (gen + 1),
            duration=0.001 * (gen + 1),
            total_duration=1.0 * (gen + 1),
        )
        items.append(item)
    return items


@pytest.fixture
def mock_logger() -> Mock:
    """Create a mock logger instance.

    Returns:
        Mock logging.Logger instance.
    """
    return Mock(spec=logging.Logger)


@pytest.fixture
def mock_process() -> Mock:
    """Create a mock subprocess.Popen instance.

    Returns:
        Mock subprocess.Popen instance with common attributes.
    """
    process = Mock(spec=subprocess.Popen)
    process.pid = 12345
    process.returncode = 0
    process.communicate.return_value = (b"stdout data", b"stderr data")
    return process

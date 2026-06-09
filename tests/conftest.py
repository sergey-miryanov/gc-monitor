from __future__ import annotations

import logging
import subprocess
from unittest.mock import Mock

import pytest

from gcmon.monitor import EventsMonitor
from gcmon.protocol import TGCStatsInfo
from gcmon.stats import StreamingStats
from gcmon.target_process import ExternalProcess
from tests.helpers import MockExporter, create_mock_stats_item


DEFAULT_PID: int = 12345


def pytest_addoption(parser):
    parser.addoption('--count', default=1, type=int, metavar='count', help='Run each test the specified number of times')

def pytest_collection_modifyitems(session, config, items):
    count = config.option.count
    items[:] = items * count  # add each test multiple times


@pytest.fixture(autouse=True)
def _caplog_gcmon(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Auto-configure gcmon logger to INFO level for caplog."""
    logger = logging.getLogger("gcmon")
    original_level = logger.level
    try:
        logger.setLevel(logging.INFO)
        yield caplog
    finally:
        logger.setLevel(original_level)


@pytest.fixture
def mock_stats_item() -> TGCStatsInfo:
    """Create a mock StatsItem with default values (gen=0).

    Note: ts_start/ts_stop are in nanoseconds (int), duration is in seconds (float).

    Returns:
        GCStatsItem dict with all required fields.
    """
    return create_mock_stats_item()


@pytest.fixture
def mock_stats_item_batch() -> list[TGCStatsInfo]:
    """Create a batch of mock GCStatsItem instances with incrementing values.

    Returns:
        List of 3 GCStatsItem dicts with different generations.
    """
    items: list[TGCStatsInfo] = []
    for gen in range(3):
        item = create_mock_stats_item(
            gen=gen,
            ts_start=1_000_000_000 + gen * 100_000_000,
            ts_stop=1_005_000_000 + gen * 100_000_000,
            collections=10 * (gen + 1),
            collected=50 * (gen + 1),
            uncollectable=gen,
            candidates=20 * (gen + 1),
            heap_size=1_000_000 * (gen + 1),
            duration=0.001 * (gen + 1),
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


@pytest.fixture
def exporter() -> MockExporter:
    return MockExporter()


@pytest.fixture
def process() -> ExternalProcess:
    return ExternalProcess(pid=12345)


@pytest.fixture
def stats() -> StreamingStats:
    return StreamingStats()


@pytest.fixture
def monitor(exporter, process: ExternalProcess, stats: StreamingStats) -> EventsMonitor:
    return EventsMonitor(process, exporter, stats)


@pytest.fixture
def make_monitor(exporter, stats):
    def _make(pid: int = 12345, exp=None):
        proc = ExternalProcess(pid=pid)
        return EventsMonitor(proc, exp or exporter, stats)
    return _make


@pytest.fixture
def env_module():
    """Provide the _env module for testing."""
    from gcmon import _env
    return _env

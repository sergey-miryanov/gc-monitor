"""Tests for GCMonitor."""

from collections.abc import Callable, Generator
from unittest.mock import MagicMock, patch

import pytest

from gcmon.monitor import EventsMonitor
from gcmon.poll_status import PollStatus
from gcmon.protocol import TGCStatsInfo
from gcmon.stats import StreamingStats
from tests.helpers import MockExporter, create_mock_stats_item

NO_EVENTS: list[TGCStatsInfo] = []

# =============================================================================
# Local fixtures
# =============================================================================


@pytest.fixture
def mock_gc_stats() -> Generator[MagicMock]:
    with patch("gcmon.monitor.get_gc_stats") as mock:
        yield mock


@pytest.fixture
def mock_monotonic() -> Generator[MagicMock]:
    """Patch the clock used to measure read time, in nanoseconds."""
    with patch("gcmon.monitor.time.monotonic_ns") as mock:
        yield mock


# =============================================================================
# GCMonitor tests
# =============================================================================


class TestGCMonitor:
    def test_init(self, monitor: EventsMonitor) -> None:
        assert monitor.is_enabled
        assert monitor.pid == 12345

    def test_poll(self, exporter: MockExporter, monitor: EventsMonitor, mock_gc_stats: MagicMock) -> None:
        item = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_005_000_000)

        mock_gc_stats.return_value = [item]
        result = monitor.poll(12345)

        assert result == PollStatus.OK
        assert len(exporter.events) == 1

    def test_poll_duplicate_records(
        self, exporter: MockExporter, monitor: EventsMonitor, mock_gc_stats: MagicMock
    ) -> None:
        """The monitor identifies a record by `collections`, so two slots
        reporting the same value are one collection seen twice."""
        item1 = create_mock_stats_item(collections=50, ts_start=1_000_000_000, ts_stop=1_005_000_000)
        item2 = create_mock_stats_item(collections=50, ts_start=1_000_000_000, ts_stop=1_006_000_000)
        item3 = create_mock_stats_item(collections=51, ts_start=2_000_000_000, ts_stop=2_005_000_000)

        mock_gc_stats.return_value = [item1, item2, item3]
        monitor.poll(12345)

        assert len(exporter.events) == 2
        assert exporter.events[0].ts_start == 1_000_000_000
        assert exporter.events[1].ts_start == 2_000_000_000

    @pytest.mark.parametrize(
        "expected_status, error_msg",
        [
            (PollStatus.INVALID_PROCESS, "Failed to initialize process handle"),
            (PollStatus.INVALID_PROCESS, "Failed to get Python runtime address"),
            (PollStatus.INVALID_PROCESS, "Failed to read debug offsets"),
            (PollStatus.INVALID_PROCESS, "Invalid debug offsets found"),
            (PollStatus.INVALID_PROCESS, "Some other error"),
        ],
    )
    def test_poll_runtime_error(
        self, monitor: EventsMonitor, expected_status: PollStatus, error_msg: str, mock_gc_stats: MagicMock
    ) -> None:
        mock_gc_stats.side_effect = RuntimeError(error_msg)
        result = monitor.poll(12345)

        assert result == expected_status

    def test_poll_general_exception(self, monitor: EventsMonitor, mock_gc_stats: MagicMock) -> None:
        mock_gc_stats.side_effect = ValueError("Unexpected error")
        result = monitor.poll(12345)

        assert result == PollStatus.FAIL

    def test_poll_after_stop(self, monitor: EventsMonitor) -> None:
        monitor.stop()
        assert monitor.poll(12345) == PollStatus.FAIL

    def test_stop(self, exporter: MockExporter, monitor: EventsMonitor) -> None:
        monitor.stop()
        assert not monitor.is_enabled
        assert exporter._close_called

    def test_stop_idempotent(self, monitor: EventsMonitor) -> None:
        monitor.stop()
        monitor.stop()
        assert not monitor.is_enabled


class TestGCMonitorReadTime:
    """Tests for read time tracking around get_gc_stats."""

    def test_poll_records_read_time(
        self, monitor: EventsMonitor, stats: StreamingStats, mock_gc_stats: MagicMock, mock_monotonic: MagicMock
    ) -> None:
        mock_monotonic.side_effect = [1_000_000_000, 1_002_500_000]
        mock_gc_stats.return_value = [create_mock_stats_item()]

        assert monitor.poll(12345) == PollStatus.OK

        # 2.5 ms between the two monotonic_ns readings, stored as nanoseconds
        assert stats.read_time.count() == 1
        assert stats.read_time.sum() == 2_500_000

    def test_poll_records_read_time_without_events(
        self, monitor: EventsMonitor, stats: StreamingStats, mock_gc_stats: MagicMock
    ) -> None:
        mock_gc_stats.return_value = NO_EVENTS

        assert monitor.poll(12345) == PollStatus.OK

        assert stats.count() == 0
        assert stats.read_time.count() == 1

    def test_poll_keeps_sub_microsecond_read_time(
        self, monitor: EventsMonitor, stats: StreamingStats, mock_gc_stats: MagicMock, mock_monotonic: MagicMock
    ) -> None:
        mock_monotonic.side_effect = [1_000_000_000, 1_000_000_750]
        mock_gc_stats.return_value = NO_EVENTS

        assert monitor.poll(12345) == PollStatus.OK

        assert stats.read_time.sum() == 750

    def test_read_time_accumulates_over_polls(
        self, monitor: EventsMonitor, stats: StreamingStats, mock_gc_stats: MagicMock, mock_monotonic: MagicMock
    ) -> None:
        mock_monotonic.side_effect = [0, 1_000_000, 5_000_000, 8_000_000]
        mock_gc_stats.return_value = NO_EVENTS

        monitor.poll(12345)
        monitor.poll(12345)

        assert stats.read_time.count() == 2
        assert stats.read_time.sum() == 4_000_000
        assert stats.read_time.average() == 2_000_000

    def test_read_time_shared_across_pids(
        self,
        make_monitor: Callable[..., EventsMonitor],
        stats: StreamingStats,
        mock_gc_stats: MagicMock,
    ) -> None:
        mock_gc_stats.return_value = NO_EVENTS

        make_monitor(pid=111).poll(111)
        make_monitor(pid=222).poll(222)

        assert stats.read_time.count() == 2

    def test_read_time_not_recorded_on_failed_read(
        self, monitor: EventsMonitor, stats: StreamingStats, mock_gc_stats: MagicMock
    ) -> None:
        mock_gc_stats.side_effect = RuntimeError("Failed to initialize process handle")

        assert monitor.poll(12345) == PollStatus.INVALID_PROCESS

        assert stats.read_time.count() == 0

    def test_read_time_not_recorded_after_stop(
        self, monitor: EventsMonitor, stats: StreamingStats, mock_gc_stats: MagicMock
    ) -> None:
        mock_gc_stats.return_value = NO_EVENTS
        monitor.stop()

        assert monitor.poll(12345) == PollStatus.FAIL

        assert stats.read_time.count() == 0
        mock_gc_stats.assert_not_called()

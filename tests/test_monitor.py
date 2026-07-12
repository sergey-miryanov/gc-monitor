"""Tests for GCMonitor."""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from gcmon.monitor import EventsMonitor
from gcmon.poll_status import PollStatus
from tests.helpers import MockExporter, create_mock_stats_item

# =============================================================================
# Local fixtures
# =============================================================================


@pytest.fixture
def mock_gc_stats() -> Generator[MagicMock]:
    with patch("gcmon.monitor.get_gc_stats") as mock:
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

    def test_poll_duplicate_timestamps(
        self, exporter: MockExporter, monitor: EventsMonitor, mock_gc_stats: MagicMock
    ) -> None:
        item1 = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_005_000_000)
        item2 = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_006_000_000)
        item3 = create_mock_stats_item(ts_start=2_000_000_000, ts_stop=2_005_000_000)

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

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from gcmon.data import GCStatsInfo
from gcmon.monitor import EventsMonitor, create_monitor
from gcmon.stats import StreamingStats
from gcmon.target_process import ExternalProcess
from tests.helpers import MockExporter, create_mock_stats_item


@pytest.fixture
def mock_gc_stats() -> Generator[GCStatsInfo]:
    item = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_005_000_000)
    with patch("gcmon.monitor.get_gc_stats", return_value=[item]):
        yield item


@pytest.fixture
def mock_stats_update(monitor: EventsMonitor) -> Generator[MagicMock]:
    with patch.object(monitor._stats, "update") as mock:
        yield mock


class TestEventsMonitorExtra:
    def test_get_child_pids(self, monitor: EventsMonitor) -> None:
        with patch("gcmon.monitor.get_child_pids", return_value=[999, 888]) as mock_get:
            children = monitor.get_child_pids()

        mock_get.assert_called_once_with(12345, recursive=True)
        assert children == [999, 888]

    def test_get_child_pids_exception_returns_none(self, monitor: EventsMonitor) -> None:
        """None rather than [], so the caller can tell a failed listing from
        a target with no children and skip pruning that tick."""
        with patch("gcmon.monitor.get_child_pids", side_effect=Exception("boom")) as mock_get:
            children = monitor.get_child_pids()

        mock_get.assert_called_once_with(12345, recursive=True)
        assert children is None

    def test_exporter_property(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        assert monitor.exporter is exporter

    def test_context_manager_enter_exit(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        assert monitor.is_enabled
        with monitor as m:
            assert m is monitor
            assert monitor.is_enabled
        assert not monitor.is_enabled

    def test_poll_updates_stats(
        self, monitor: EventsMonitor, mock_gc_stats: GCStatsInfo, mock_stats_update: MagicMock
    ) -> None:
        monitor.poll(12345)

        mock_stats_update.assert_called_once_with(12345, mock_gc_stats)

    def test_poll_skips_invalid_timestamp_event(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        item = create_mock_stats_item(ts_start=2_000, ts_stop=1_000)

        with patch("gcmon.monitor.get_gc_stats", return_value=[item]):
            monitor.poll(12345)

        assert exporter.events == []

    def test_poll_skips_equal_timestamp_event(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        item = create_mock_stats_item(ts_start=1_000, ts_stop=1_000)

        with patch("gcmon.monitor.get_gc_stats", return_value=[item]):
            monitor.poll(12345)

        assert exporter.events == []

    def test_poll_tracks_last_timestamp_per_pid(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        """A child PID's events are not suppressed by a later timestamp seen on
        another PID. One monitor polls the target and every child, and their
        event streams interleave in time."""
        per_pid = {
            12345: [create_mock_stats_item(collections=50, ts_start=5_000, ts_stop=5_100)],
            999: [
                create_mock_stats_item(collections=7, ts_start=4_000, ts_stop=4_100),
                create_mock_stats_item(collections=8, ts_start=6_000, ts_stop=6_100),
            ],
        }

        with patch("gcmon.monitor.get_gc_stats", side_effect=lambda pid, **_: per_pid[pid]):
            monitor.poll(12345)
            monitor.poll(999)

        assert [e.ts_start for e in exporter.events] == [5_000, 4_000, 6_000]

    def test_poll_still_skips_already_seen_timestamps_for_same_pid(
        self, monitor: EventsMonitor, exporter: MockExporter
    ) -> None:
        item = create_mock_stats_item(ts_start=5_000, ts_stop=5_100)

        with patch("gcmon.monitor.get_gc_stats", return_value=[item]):
            monitor.poll(12345)
            monitor.poll(12345)

        assert [e.ts_start for e in exporter.events] == [5_000]


class TestCreateMonitor:
    def test_returns_events_monitor(
        self, exporter: MockExporter, process: ExternalProcess, stats: StreamingStats
    ) -> None:
        result = create_monitor(process, exporter, stats)
        assert isinstance(result, EventsMonitor)
        assert result.is_enabled
        assert result.pid == 12345
        assert result.exporter is exporter

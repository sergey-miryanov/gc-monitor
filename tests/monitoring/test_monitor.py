from unittest.mock import patch

import pytest

from gcmon.monitor import EventsMonitor, create_monitor
from gcmon.poll_status import PollStatus
from tests.helpers import MockExporter, create_mock_stats_item


class TestEventsMonitorExtra:
    def test_get_child_pids(self, monitor: EventsMonitor) -> None:
        with patch("gcmon.monitor.get_child_pids", return_value=[999, 888]) as mock_get:
            children = monitor.get_child_pids()

        mock_get.assert_called_once_with(12345, recursive=True)
        assert children == [999, 888]

    def test_get_child_pids_exception_returns_empty(
        self, monitor: EventsMonitor
    ) -> None:
        with patch(
            "gcmon.monitor.get_child_pids", side_effect=Exception("boom")
        ) as mock_get:
            children = monitor.get_child_pids()

        mock_get.assert_called_once_with(12345, recursive=True)
        assert children == []

    def test_exporter_property(
        self, monitor: EventsMonitor, exporter: MockExporter
    ) -> None:
        assert monitor.exporter is exporter

    def test_context_manager_enter_exit(
        self, monitor: EventsMonitor, exporter: MockExporter
    ) -> None:
        assert monitor.is_enabled
        with monitor as m:
            assert m is monitor
            assert monitor.is_enabled
        assert not monitor.is_enabled
        assert exporter._close_called

    def test_poll_updates_stats(self, monitor: EventsMonitor) -> None:
        item = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_005_000_000)

        with patch("gcmon.monitor.get_gc_stats", return_value=[item]):
            with patch.object(monitor._stats, "update") as mock_stats_update:
                monitor.poll(12345)

        mock_stats_update.assert_called_once_with(12345, item)

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


class TestCreateMonitor:
    def test_returns_events_monitor(
        self, exporter: MockExporter, process, stats
    ) -> None:
        result = create_monitor(process, exporter, stats)
        assert isinstance(result, EventsMonitor)
        assert result.is_enabled
        assert result.pid == 12345
        assert result.exporter is exporter

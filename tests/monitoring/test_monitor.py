from unittest.mock import patch

import pytest

from gcmon.monitor import EventsMonitor, create_monitor
from gcmon.poll_status import PollStatus, ProcessLifecycle
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


class TestEventsMonitorLifecycle:
    """Tests for the STARTED / DIED transitions reported via
    ``EventsMonitor.poll`` and ``EventsMonitor.stop``."""

    def test_first_ok_poll_emits_started(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        with patch("gcmon.monitor.get_gc_stats", return_value=[]):
            status = monitor.poll(12345)
        assert status is PollStatus.OK
        assert exporter.lifecycle_events == [
            (12345, ProcessLifecycle.STARTED, exporter.lifecycle_events[0][2]),
        ]
        assert exporter.lifecycle_events[0][2] > 0  # monotonic_ns

    def test_subsequent_ok_polls_do_not_re_emit_started(
        self, monitor: EventsMonitor, exporter: MockExporter,
    ) -> None:
        with patch("gcmon.monitor.get_gc_stats", return_value=[]):
            monitor.poll(12345)
            monitor.poll(12345)
            monitor.poll(12345)
        kinds = [kind for _, kind, _ in exporter.lifecycle_events]
        assert kinds == [ProcessLifecycle.STARTED]

    def test_ok_then_invalid_emits_died_once(
        self, monitor: EventsMonitor, exporter: MockExporter,
    ) -> None:
        with patch("gcmon.monitor.get_gc_stats", return_value=[]):
            monitor.poll(12345)
        with patch("gcmon.monitor.get_gc_stats", side_effect=RuntimeError("gone")):
            status = monitor.poll(12345)
        assert status is PollStatus.INVALID_PROCESS
        kinds = [kind for _, kind, _ in exporter.lifecycle_events]
        assert kinds == [ProcessLifecycle.STARTED, ProcessLifecycle.DIED]

    def test_invalid_without_prior_ok_does_not_emit_died(
        self, monitor: EventsMonitor, exporter: MockExporter,
    ) -> None:
        with patch("gcmon.monitor.get_gc_stats", side_effect=PermissionError("nope")):
            status = monitor.poll(12345)
        assert status is PollStatus.INVALID_PROCESS
        assert exporter.lifecycle_events == []

    def test_repeated_invalid_does_not_emit_died_twice(
        self, monitor: EventsMonitor, exporter: MockExporter,
    ) -> None:
        with patch("gcmon.monitor.get_gc_stats", return_value=[]):
            monitor.poll(12345)
        with patch("gcmon.monitor.get_gc_stats", side_effect=RuntimeError("gone")):
            monitor.poll(12345)
            monitor.poll(12345)
        kinds = [kind for _, kind, _ in exporter.lifecycle_events]
        assert kinds == [ProcessLifecycle.STARTED, ProcessLifecycle.DIED]

    def test_multiple_pids_track_lifecycle_independently(
        self, monitor: EventsMonitor, exporter: MockExporter,
    ) -> None:
        with patch("gcmon.monitor.get_gc_stats", return_value=[]):
            monitor.poll(12345)
            monitor.poll(99999)
        with patch("gcmon.monitor.get_gc_stats", side_effect=RuntimeError("gone")):
            monitor.poll(12345)
        # pid 99999 should still be alive.
        kinds = [(pid, kind) for pid, kind, _ in exporter.lifecycle_events]
        assert kinds == [
            (12345, ProcessLifecycle.STARTED),
            (99999, ProcessLifecycle.STARTED),
            (12345, ProcessLifecycle.DIED),
        ]

    def test_stop_emits_died_for_alive_pids(
        self, monitor: EventsMonitor, exporter: MockExporter,
    ) -> None:
        with patch("gcmon.monitor.get_gc_stats", return_value=[]):
            monitor.poll(12345)
            monitor.poll(99999)
        monitor.stop()
        kinds = [(pid, kind) for pid, kind, _ in exporter.lifecycle_events]
        assert kinds == [
            (12345, ProcessLifecycle.STARTED),
            (99999, ProcessLifecycle.STARTED),
            (12345, ProcessLifecycle.DIED),
            (99999, ProcessLifecycle.DIED),
        ]
        assert exporter._close_called

    def test_stop_emits_no_died_when_no_pids_were_alive(
        self, monitor: EventsMonitor, exporter: MockExporter,
    ) -> None:
        monitor.stop()
        assert exporter.lifecycle_events == []
        assert exporter._close_called

    def test_stop_is_idempotent(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        with patch("gcmon.monitor.get_gc_stats", return_value=[]):
            monitor.poll(12345)
        monitor.stop()
        first = list(exporter.lifecycle_events)
        monitor.stop()  # second call must not re-emit DIED
        assert exporter.lifecycle_events == first

    def test_fail_does_not_emit_lifecycle(
        self, monitor: EventsMonitor, exporter: MockExporter,
    ) -> None:
        with patch("gcmon.monitor.get_gc_stats", side_effect=Exception("boom")):
            status = monitor.poll(12345)
        assert status is PollStatus.FAIL
        assert exporter.lifecycle_events == []

    def test_mark_pid_died_emits_died_for_alive_pid(
        self, monitor: EventsMonitor, exporter: MockExporter,
    ) -> None:
        with patch("gcmon.monitor.get_gc_stats", return_value=[]):
            monitor.poll(12345)
        assert monitor.mark_pid_died(12345) is True
        kinds = [kind for _, kind, _ in exporter.lifecycle_events]
        assert kinds == [ProcessLifecycle.STARTED, ProcessLifecycle.DIED]

    def test_mark_pid_died_returns_false_for_unknown_pid(
        self, monitor: EventsMonitor, exporter: MockExporter,
    ) -> None:
        assert monitor.mark_pid_died(99999) is False
        assert exporter.lifecycle_events == []

    def test_mark_pid_died_is_idempotent(
        self, monitor: EventsMonitor, exporter: MockExporter,
    ) -> None:
        with patch("gcmon.monitor.get_gc_stats", return_value=[]):
            monitor.poll(12345)
        assert monitor.mark_pid_died(12345) is True
        # Second call: no longer alive, must not re-emit.
        assert monitor.mark_pid_died(12345) is False
        kinds = [kind for _, kind, _ in exporter.lifecycle_events]
        assert kinds == [ProcessLifecycle.STARTED, ProcessLifecycle.DIED]

    def test_mark_pid_died_per_pid(
        self, monitor: EventsMonitor, exporter: MockExporter,
    ) -> None:
        with patch("gcmon.monitor.get_gc_stats", return_value=[]):
            monitor.poll(12345)
            monitor.poll(99999)
        # 12345 is alive; mark it died.
        assert monitor.mark_pid_died(12345) is True
        # 99999 is still alive; reporting it died again must succeed.
        assert monitor.mark_pid_died(99999) is True
        kinds = [kind for _, kind, _ in exporter.lifecycle_events]
        assert kinds == [
            ProcessLifecycle.STARTED,
            ProcessLifecycle.STARTED,
            ProcessLifecycle.DIED,
            ProcessLifecycle.DIED,
        ]


class TestCreateMonitor:
    def test_returns_events_monitor(
        self, exporter: MockExporter, process, stats
    ) -> None:
        result = create_monitor(process, exporter, stats)
        assert isinstance(result, EventsMonitor)
        assert result.is_enabled
        assert result.pid == 12345
        assert result.exporter is exporter

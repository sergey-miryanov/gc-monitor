"""Tests for GCMonitorThread and GCMonitor."""

from unittest.mock import patch

import pytest

from gcmon.monitor import EventsMonitor
from gcmon.monitor_thread import MonitorThread
from gcmon.poll_status import PollStatus
from gcmon.wait_policy import StartupTimeoutPolicy

from tests.helpers import MockExporter, create_mock_stats_item


# =============================================================================
# Local fixtures
# =============================================================================


@pytest.fixture
def mock_gc_stats():
    with patch("gcmon.monitor.get_gc_stats") as mock:
        yield mock


@pytest.fixture
def thread_factory():
    def _make(rate: float = 0.1) -> MonitorThread:
        return MonitorThread(lambda: StartupTimeoutPolicy(5), rate=rate)
    return _make


# =============================================================================
# GCMonitor tests
# =============================================================================


class TestGCMonitor:
    def test_init(self, monitor: EventsMonitor) -> None:
        assert monitor.is_enabled
        assert monitor.pid == 12345

    def test_poll(self, exporter: MockExporter, monitor: EventsMonitor) -> None:
        item = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_005_000_000)

        with patch("gcmon.monitor.get_gc_stats", return_value=[item]):
            result = monitor.poll(12345)

        assert result == PollStatus.OK
        assert len(exporter.events) == 1

    def test_poll_duplicate_timestamps(self, exporter: MockExporter, monitor: EventsMonitor) -> None:
        item1 = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_005_000_000)
        item2 = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_006_000_000)
        item3 = create_mock_stats_item(ts_start=2_000_000_000, ts_stop=2_005_000_000)

        with patch("gcmon.monitor.get_gc_stats", return_value=[item1, item2, item3]):
            monitor.poll(12345)

        assert len(exporter.events) == 2
        assert exporter.events[0].ts_start == 1_000_000_000
        assert exporter.events[1].ts_start == 2_000_000_000

    @pytest.mark.parametrize("expected_status, error_msg", [
        (PollStatus.INVALID_PROCESS, "Failed to initialize process handle"),
        (PollStatus.INVALID_PROCESS, "Failed to get Python runtime address"),
        (PollStatus.INVALID_PROCESS, "Failed to read debug offsets"),
        (PollStatus.INVALID_PYTHON, "Invalid debug offsets found"),
        (PollStatus.FAIL, "Some other error"),
    ])
    def test_poll_runtime_error(self, monitor: EventsMonitor, expected_status: PollStatus, error_msg: str) -> None:
        with patch("gcmon.monitor.get_gc_stats", side_effect=RuntimeError(error_msg)):
            result = monitor.poll(12345)

        assert result == expected_status

    def test_poll_general_exception(self, monitor: EventsMonitor) -> None:
        with patch("gcmon.monitor.get_gc_stats", side_effect=ValueError("Unexpected error")):
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


# =============================================================================
# MonitorThread tests
# =============================================================================


class TestGCMonitorThread:
    def test_init(self, thread_factory) -> None:
        thread = thread_factory()
        assert not thread.is_running
        assert thread.monitor_count == 0

    def test_add_monitor(self, thread_factory, monitor: EventsMonitor) -> None:
        thread = thread_factory()
        thread.add_monitor(monitor)
        assert thread.monitor_count == 1

    def test_remove_monitor(self, thread_factory, monitor: EventsMonitor) -> None:
        thread = thread_factory()
        thread.add_monitor(monitor)
        assert thread.remove_monitor(monitor) is True
        assert thread.monitor_count == 0
        assert not monitor.is_enabled

    def test_remove_nonexistent_monitor(self, thread_factory, monitor: EventsMonitor) -> None:
        thread = thread_factory()
        assert thread.remove_monitor(monitor) is False

    def test_start_stop(self, thread_factory, make_monitor, mock_gc_stats) -> None:
        thread = thread_factory()
        item = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_005_000_000)
        mock_gc_stats.return_value = [item]
        exporter = MockExporter()
        monitor = make_monitor(exp=exporter)

        thread.add_monitor(monitor)
        thread.start()
        assert exporter.wait_for_event(timeout=0.5)
        thread.stop()

        assert not thread.is_running
        assert not monitor.is_enabled

    def test_start_twice_raises_error(self, thread_factory, make_monitor) -> None:
        thread = thread_factory()
        exporter = MockExporter()
        monitor = make_monitor(exp=exporter)

        thread.add_monitor(monitor)
        thread.start()
        with pytest.raises(RuntimeError, match="already running"):
            thread.start()
        thread.stop()

    def test_multiple_monitors(self, thread_factory, make_monitor, mock_gc_stats) -> None:
        thread = thread_factory(rate=0.05)

        item1 = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_005_000_000)
        item2 = create_mock_stats_item(ts_start=2_000_000_000, ts_stop=2_005_000_000)

        def side_effect(pid: int, all_interpreters: bool = False):
            return [item1] if pid == 12345 else [item2]

        mock_gc_stats.side_effect = side_effect

        exporter1 = MockExporter()
        exporter2 = MockExporter()
        monitor1 = make_monitor(exp=exporter1)
        monitor2 = make_monitor(pid=54321, exp=exporter2)

        thread.add_monitor(monitor1)
        thread.add_monitor(monitor2)
        thread.start()

        assert exporter1.wait_for_event(timeout=0.5)
        assert exporter2.wait_for_event(timeout=0.5)

        thread.stop()

        assert len(exporter1.events) >= 1
        assert len(exporter2.events) >= 1

    def test_dynamic_add_during_runtime(self, thread_factory, make_monitor, mock_gc_stats) -> None:
        thread = thread_factory(rate=0.05)

        item1 = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_005_000_000)
        exporter1 = MockExporter()
        monitor1 = make_monitor(exp=exporter1)

        mock_gc_stats.side_effect = lambda pid, all_interpreters=False: (
            [item1] if pid == 12345
            else [create_mock_stats_item(ts_start=2_000_000_000, ts_stop=2_005_000_000)]
        )

        thread.add_monitor(monitor1)
        thread.start()
        assert exporter1.wait_for_event(timeout=0.5)

        exporter2 = MockExporter()
        monitor2 = make_monitor(pid=54321, exp=exporter2)
        thread.add_monitor(monitor2)
        assert exporter2.wait_for_event(timeout=0.5)

        thread.stop()

        assert len(exporter1.events) >= 1
        assert len(exporter2.events) >= 1

    def test_empty_monitor_list(self, thread_factory) -> None:
        thread = thread_factory(rate=0.05)
        thread.start()
        assert thread.is_running
        thread.stop()
        assert not thread.is_running

    def test_monitor_error_handling(self, thread_factory, make_monitor, mock_gc_stats) -> None:
        thread = thread_factory(rate=0.05)

        item2 = create_mock_stats_item(ts_start=2_000_000_000, ts_stop=2_005_000_000)

        def _mock_gc_stats(pid, all_interpreters=False):
            if pid == 12345:
                raise RuntimeError("Failed to initialize process handle")
            return [item2]

        mock_gc_stats.side_effect = _mock_gc_stats

        exporter1 = MockExporter()
        exporter2 = MockExporter()
        monitor1 = make_monitor(exp=exporter1)
        monitor2 = make_monitor(pid=54321, exp=exporter2)

        thread.add_monitor(monitor1)
        thread.add_monitor(monitor2)
        thread.start()

        assert exporter2.wait_for_event(timeout=0.5)
        thread.stop()

        assert len(exporter2.events) >= 1
        assert not monitor1.is_enabled

    def test_close(self, thread_factory, make_monitor, mock_gc_stats) -> None:
        thread = thread_factory()
        item = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_005_000_000)
        mock_gc_stats.return_value = [item]
        exporter = MockExporter()
        monitor = make_monitor(exp=exporter)

        thread.add_monitor(monitor)
        thread.start()
        assert exporter.wait_for_event(timeout=0.5)
        thread.close()

        assert not thread.is_running
        assert not monitor.is_enabled

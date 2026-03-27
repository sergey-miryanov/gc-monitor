"""Tests for GCMonitorThread and refactored GCMonitor."""

import threading
from unittest.mock import Mock, call

import pytest

from gc_monitor.core import GCMonitor, GCMonitorThread
from gc_monitor.exporter import GCMonitorExporter
from gc_monitor.protocol import MonitorHandler, StatsItem


class MockHandler:
    """Mock MonitorHandler for testing."""

    def __init__(self, events_per_read: list[list[StatsItem]] | None = None) -> None:
        self.events_per_read = events_per_read or []
        self._read_index = 0
        self._close_called = False
        self._read_count = 0
        self._read_event = threading.Event()

    def read(self) -> list[StatsItem]:
        self._read_count += 1
        self._read_event.set()  # Signal that read was called
        if self._read_index < len(self.events_per_read):
            events = self.events_per_read[self._read_index]
            self._read_index += 1
            return events
        return []

    def close(self) -> None:
        self._close_called = True

    def wait_for_read(self, timeout: float = 1.0) -> bool:
        """Wait for read() to be called."""
        result = self._read_event.wait(timeout=timeout)
        self._read_event.clear()
        return result


class MockExporter(GCMonitorExporter):
    """Mock exporter for testing."""

    def __init__(self, pid: int) -> None:
        super().__init__(pid)
        self.events: list[StatsItem] = []
        self._close_called = False
        self._event_added = threading.Event()

    def add_event(self, stats_item: StatsItem) -> None:
        self.events.append(stats_item)
        self._event_added.set()  # Signal that event was added

    def close(self) -> None:
        self._close_called = True

    def get_event_count(self) -> int:
        return len(self.events)

    def wait_for_event(self, timeout: float = 1.0) -> bool:
        """Wait for an event to be added."""
        result = self._event_added.wait(timeout=timeout)
        self._event_added.clear()
        return result


def _create_mock_stats_item(ts: int = 1000) -> StatsItem:
    """Create a mock StatsItem with given timestamp."""
    item = Mock(spec=StatsItem)
    item.gen = 0
    item.ts = ts
    item.collections = 1
    item.collected = 10
    item.uncollectable = 0
    item.candidates = 5
    item.object_visits = 100
    item.objects_transitively_reachable = 50
    item.objects_not_transitively_reachable = 50
    item.heap_size = 1024
    item.work_to_do = 0
    item.duration = 0.001
    item.total_duration = 0.001
    return item  # type: ignore[return-value]


class TestGCMonitor:
    """Tests for refactored GCMonitor class."""

    def test_monitor_init(self) -> None:
        """Test monitor initialization."""
        handler = MockHandler()
        exporter = MockExporter(pid=12345)
        monitor = GCMonitor(handler, exporter)

        assert monitor.is_enabled
        assert monitor.pid == 12345

    def test_monitor_poll(self) -> None:
        """Test single poll iteration."""
        item = _create_mock_stats_item(ts=1000)
        handler = MockHandler(events_per_read=[[item]])
        exporter = MockExporter(pid=12345)
        monitor = GCMonitor(handler, exporter)

        result = monitor.poll()

        assert result is True
        assert len(exporter.events) == 1
        assert exporter.events[0].ts == 1000

    def test_monitor_poll_duplicate_timestamps(self) -> None:
        """Test that duplicate timestamps are filtered."""
        item1 = _create_mock_stats_item(ts=1000)
        item2 = _create_mock_stats_item(ts=1000)  # Same timestamp
        item3 = _create_mock_stats_item(ts=2000)  # New timestamp
        handler = MockHandler(events_per_read=[[item1, item2, item3]])
        exporter = MockExporter(pid=12345)
        monitor = GCMonitor(handler, exporter)

        monitor.poll()

        # Only first and third items should be exported (unique timestamps)
        assert len(exporter.events) == 2
        assert exporter.events[0].ts == 1000
        assert exporter.events[1].ts == 2000

    def test_monitor_poll_runtime_error(self) -> None:
        """Test that RuntimeError disables monitor."""
        handler = Mock(spec=MonitorHandler)
        handler.read.side_effect = RuntimeError("Process terminated")
        exporter = MockExporter(pid=12345)
        monitor = GCMonitor(handler, exporter)

        assert monitor.poll() is False
        assert not monitor.is_enabled

    def test_monitor_stop(self) -> None:
        """Test stopping monitor."""
        handler = MockHandler()
        exporter = MockExporter(pid=12345)
        monitor = GCMonitor(handler, exporter)

        monitor.stop()

        assert not monitor.is_enabled
        assert handler._close_called
        assert exporter._close_called

    def test_monitor_stop_idempotent(self) -> None:
        """Test that stop can be called multiple times."""
        handler = MockHandler()
        exporter = MockExporter(pid=12345)
        monitor = GCMonitor(handler, exporter)

        monitor.stop()
        monitor.stop()  # Should not raise

        assert not monitor.is_enabled


class TestGCMonitorThread:
    """Tests for GCMonitorThread class."""

    def test_thread_init(self) -> None:
        """Test thread initialization."""
        thread = GCMonitorThread(rate=0.1)

        assert not thread.is_running
        assert thread.monitor_count == 0

    def test_thread_add_monitor(self) -> None:
        """Test adding a monitor to thread."""
        thread = GCMonitorThread(rate=0.1)
        handler = MockHandler()
        exporter = MockExporter(pid=12345)
        monitor = GCMonitor(handler, exporter)

        thread.add_monitor(monitor)

        assert thread.monitor_count == 1

    def test_thread_remove_monitor(self) -> None:
        """Test removing a monitor from thread."""
        thread = GCMonitorThread(rate=0.1)
        handler = MockHandler()
        exporter = MockExporter(pid=12345)
        monitor = GCMonitor(handler, exporter)

        thread.add_monitor(monitor)
        result = thread.remove_monitor(monitor)

        assert result is True
        assert thread.monitor_count == 0
        assert not monitor.is_enabled

    def test_thread_remove_nonexistent_monitor(self) -> None:
        """Test removing a monitor that wasn't added."""
        thread = GCMonitorThread(rate=0.1)
        handler = MockHandler()
        exporter = MockExporter(pid=12345)
        monitor = GCMonitor(handler, exporter)

        result = thread.remove_monitor(monitor)

        assert result is False

    def test_thread_start_stop(self) -> None:
        """Test starting and stopping thread."""
        thread = GCMonitorThread(rate=0.1)
        handler = MockHandler(events_per_read=[[_create_mock_stats_item()]])
        exporter = MockExporter(pid=12345)
        monitor = GCMonitor(handler, exporter)

        thread.add_monitor(monitor)
        thread.start()

        assert thread.is_running

        thread.stop()

        assert not thread.is_running
        assert not monitor.is_enabled

    def test_thread_start_twice_raises_error(self) -> None:
        """Test that starting thread twice raises RuntimeError."""
        thread = GCMonitorThread(rate=0.1)
        handler = MockHandler()
        exporter = MockExporter(pid=12345)
        monitor = GCMonitor(handler, exporter)

        thread.add_monitor(monitor)
        thread.start()

        with pytest.raises(RuntimeError, match="already running"):
            thread.start()

        thread.stop()

    def test_thread_multiple_monitors(self) -> None:
        """Test thread managing multiple monitors."""
        thread = GCMonitorThread(rate=0.05)

        handler1 = MockHandler(events_per_read=[[_create_mock_stats_item(ts=1000)]])
        exporter1 = MockExporter(pid=12345)
        monitor1 = GCMonitor(handler1, exporter1)

        handler2 = MockHandler(events_per_read=[[_create_mock_stats_item(ts=2000)]])
        exporter2 = MockExporter(pid=54321)
        monitor2 = GCMonitor(handler2, exporter2)

        thread.add_monitor(monitor1)
        thread.add_monitor(monitor2)
        thread.start()

        # Wait for at least one polling cycle using event-based synchronization
        # Both handlers should be read at least once
        assert handler1.wait_for_read(timeout=0.5), "Handler1 should have been polled"
        assert handler2.wait_for_read(timeout=0.5), "Handler2 should have been polled"

        thread.stop()

        # Both monitors should have polled
        assert len(exporter1.events) >= 1
        assert len(exporter2.events) >= 1

    def test_thread_dynamic_add_during_runtime(self) -> None:
        """Test adding monitor while thread is running."""
        thread = GCMonitorThread(rate=0.05)

        handler1 = MockHandler(events_per_read=[[_create_mock_stats_item(ts=1000)]])
        exporter1 = MockExporter(pid=12345)
        monitor1 = GCMonitor(handler1, exporter1)

        thread.add_monitor(monitor1)
        thread.start()

        # Wait for first poll using event-based synchronization
        assert handler1.wait_for_read(timeout=0.5), "Handler1 should have been polled"

        # Add second monitor dynamically
        handler2 = MockHandler(events_per_read=[[_create_mock_stats_item(ts=2000)]])
        exporter2 = MockExporter(pid=54321)
        monitor2 = GCMonitor(handler2, exporter2)
        thread.add_monitor(monitor2)

        # Wait for second monitor to be polled
        assert handler2.wait_for_read(timeout=0.5), "Handler2 should have been polled"

        thread.stop()

        # Both should have events
        assert len(exporter1.events) >= 1
        assert len(exporter2.events) >= 1

    def test_thread_empty_monitor_list(self) -> None:
        """Test thread with no monitors."""
        thread = GCMonitorThread(rate=0.05)
        thread.start()

        # Thread should be running even with no monitors
        # Just verify it doesn't crash
        assert thread.is_running

        thread.stop()

        # Should not crash, just exit cleanly
        assert not thread.is_running

    def test_thread_monitor_error_handling(self) -> None:
        """Test that thread continues when one monitor fails."""
        thread = GCMonitorThread(rate=0.05)

        # First monitor raises error immediately
        handler1 = Mock(spec=MonitorHandler)
        handler1.read.side_effect = RuntimeError("Error")
        exporter1 = MockExporter(pid=12345)
        monitor1 = GCMonitor(handler1, exporter1)

        # Second monitor works fine
        handler2 = MockHandler(events_per_read=[[_create_mock_stats_item(ts=2000)]])
        exporter2 = MockExporter(pid=54321)
        monitor2 = GCMonitor(handler2, exporter2)

        thread.add_monitor(monitor1)
        thread.add_monitor(monitor2)
        thread.start()

        # Wait for second monitor to be polled (first one will fail immediately)
        assert handler2.wait_for_read(timeout=0.5), "Handler2 should have been polled"

        thread.stop()

        # Second monitor should still have events
        assert len(exporter2.events) >= 1
        # First monitor should be disabled
        assert not monitor1.is_enabled

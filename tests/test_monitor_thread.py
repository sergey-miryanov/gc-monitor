"""Tests for GCMonitorThread and refactored GCMonitor."""

from unittest.mock import Mock

import pytest

from gc_monitor.core import GCMonitor, GCMonitorThread
from gc_monitor.protocol import MonitorHandler

from tests.helpers import MockHandler, MockExporter, create_mock_stats_item


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
        item = create_mock_stats_item(ts=1000)
        handler = MockHandler(events_per_read=[[item]])
        exporter = MockExporter(pid=12345)
        monitor = GCMonitor(handler, exporter)

        result = monitor.poll()

        assert result is True
        assert len(exporter.events) == 1
        assert exporter.events[0].ts == 1000

    def test_monitor_poll_duplicate_timestamps(self) -> None:
        """Test that duplicate timestamps are filtered."""
        item1 = create_mock_stats_item(ts=1000)
        item2 = create_mock_stats_item(ts=1000)  # Same timestamp
        item3 = create_mock_stats_item(ts=2000)  # New timestamp
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
        handler = MockHandler(events_per_read=[[create_mock_stats_item()]])
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

        handler1 = MockHandler(events_per_read=[[create_mock_stats_item(ts=1000)]])
        exporter1 = MockExporter(pid=12345)
        monitor1 = GCMonitor(handler1, exporter1)

        handler2 = MockHandler(events_per_read=[[create_mock_stats_item(ts=2000)]])
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

        handler1 = MockHandler(events_per_read=[[create_mock_stats_item(ts=1000)]])
        exporter1 = MockExporter(pid=12345)
        monitor1 = GCMonitor(handler1, exporter1)

        thread.add_monitor(monitor1)
        thread.start()

        # Wait for first poll using event-based synchronization
        assert handler1.wait_for_read(timeout=0.5), "Handler1 should have been polled"

        # Add second monitor dynamically
        handler2 = MockHandler(events_per_read=[[create_mock_stats_item(ts=2000)]])
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
        handler2 = MockHandler(events_per_read=[[create_mock_stats_item(ts=2000)]])
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

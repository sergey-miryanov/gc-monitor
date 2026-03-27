"""Shared helper classes and functions for gc-monitor tests."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from gc_monitor.exporter import GCMonitorExporter
from gc_monitor.protocol import StatsItem

# pyright: reportImplicitOverride=none


__all__ = [
    "MockHandler",
    "MockExporter",
    "MockGCMonitorThread",
    "create_mock_stats_item",
    "assert_valid_chrome_trace_format",
]


class MockHandler:
    """Mock MonitorHandler for testing.

    This class simulates a MonitorHandler that returns predefined events
    on each read() call. It supports event-based synchronization for tests.
    """

    def __init__(self, events_per_read: list[list[StatsItem]] | None = None) -> None:
        """Initialize the mock handler.

        Args:
            events_per_read: List of event batches to return on each read() call.
        """
        self.events_per_read = events_per_read or []
        self._read_index = 0
        self._close_called = False
        self._read_count = 0
        self._read_event = threading.Event()

    def read(self) -> list[StatsItem]:
        """Read and return the next batch of events.

        Returns:
            List of StatsItem instances for this read call.
        """
        self._read_count += 1
        self._read_event.set()  # Signal that read was called
        if self._read_index < len(self.events_per_read):
            events = self.events_per_read[self._read_index]
            self._read_index += 1
            return events
        return []

    def close(self) -> None:
        """Close the handler."""
        self._close_called = True

    def wait_for_read(self, timeout: float = 1.0) -> bool:
        """Wait for read() to be called.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if read() was called within timeout, False otherwise.
        """
        result = self._read_event.wait(timeout=timeout)
        self._read_event.clear()
        return result


class MockExporter(GCMonitorExporter):
    """Mock GCMonitorExporter for testing.

    This class simulates an exporter that collects events in memory.
    It supports event-based synchronization for tests.
    """

    def __init__(self, pid: int) -> None:
        """Initialize the mock exporter.

        Args:
            pid: Process ID to monitor.
        """
        super().__init__(pid)
        self.events: list[StatsItem] = []
        self._close_called = False
        self._event_added = threading.Event()

    def add_event(self, stats_item: StatsItem) -> None:
        """Add an event to the exporter.

        Args:
            stats_item: The stats item to add.
        """
        self.events.append(stats_item)
        self._event_added.set()  # Signal that event was added

    def close(self) -> None:
        """Close the exporter."""
        self._close_called = True

    def get_event_count(self) -> int:
        """Get the number of events added.

        Returns:
            Number of events added to the exporter.
        """
        return len(self.events)

    def wait_for_event(self, timeout: float = 1.0) -> bool:
        """Wait for an event to be added.

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if an event was added within timeout, False otherwise.
        """
        result = self._event_added.wait(timeout=timeout)
        self._event_added.clear()
        return result


class MockGCMonitorThread:
    """Mock GCMonitorThread for testing.

    This class simulates a GCMonitorThread that can be used in tests
    without actually starting a background thread.
    """

    def __init__(self) -> None:
        """Initialize the mock monitor thread."""
        self.is_running = False
        self.monitor_count = 0
        self._monitors: list[Any] = []

    def add_monitor(self, monitor: Any) -> None:
        """Add a monitor to the thread.

        Args:
            monitor: The monitor to add.
        """
        self._monitors.append(monitor)
        self.monitor_count = len(self._monitors)

    def remove_monitor(self, monitor: Any) -> bool:
        """Remove a monitor from the thread.

        Args:
            monitor: The monitor to remove.

        Returns:
            True if the monitor was removed, False if not found.
        """
        if monitor in self._monitors:
            self._monitors.remove(monitor)
            self.monitor_count = len(self._monitors)
            return True
        return False

    def start(self) -> None:
        """Start the monitor thread."""
        if self.is_running:
            raise RuntimeError("Thread is already running")
        self.is_running = True

    def stop(self) -> None:
        """Stop the monitor thread."""
        self.is_running = False
        # Disable all monitors
        for monitor in self._monitors:
            if hasattr(monitor, "stop"):
                monitor.stop()
        self._monitors.clear()
        self.monitor_count = 0


def create_mock_stats_item(
    gen: int = 0,
    ts: int = 1_500_000_000,
    collections: int = 50,
    collected: int = 200,
    uncollectable: int = 10,
    candidates: int = 40,
    object_visits: int = 600,
    objects_transitively_reachable: int = 250,
    objects_not_transitively_reachable: int = 150,
    heap_size: int = 52428800,
    work_to_do: int = 30,
    duration: float = 0.005,
    total_duration: float = 45.5,
) -> Mock:
    """Create a mock StatsItem with specified values.

    This is a factory function for creating mock StatsItem instances
    with all required fields.

    Args:
        gen: GC generation (0, 1, or 2).
        ts: Timestamp in nanoseconds.
        collections: Number of collections.
        collected: Number of objects collected.
        uncollectable: Number of uncollectable objects.
        candidates: Number of candidate objects.
        object_visits: Number of object visits.
        objects_transitively_reachable: Number of transitively reachable objects.
        objects_not_transitively_reachable: Number of non-transitively reachable objects.
        heap_size: Heap size in bytes.
        work_to_do: Amount of work to do.
        duration: Duration in seconds.
        total_duration: Total duration in seconds.

    Returns:
        Mock StatsItem instance with all fields set.
    """
    stats_item = Mock(spec=StatsItem)
    stats_item.gen = gen
    stats_item.ts = ts
    stats_item.collections = collections
    stats_item.collected = collected
    stats_item.uncollectable = uncollectable
    stats_item.candidates = candidates
    stats_item.object_visits = object_visits
    stats_item.objects_transitively_reachable = objects_transitively_reachable
    stats_item.objects_not_transitively_reachable = objects_not_transitively_reachable
    stats_item.heap_size = heap_size
    stats_item.work_to_do = work_to_do
    stats_item.duration = duration
    stats_item.total_duration = total_duration
    return stats_item


# pyright: reportUnknownVariableType=none, reportUnknownArgumentType=none
def assert_valid_chrome_trace_format(file_path: Path) -> list[dict[str, Any]]:
    """Validate that a file contains valid Chrome Trace format (JSON array of objects).

    Args:
        file_path: Path to the JSON file to validate.

    Returns:
        List of parsed event dictionaries.

    Raises:
        AssertionError: If the file is not valid Chrome Trace format.
    """
    assert file_path.exists(), f"File {file_path} does not exist"

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Check basic JSON array structure
    content_stripped = content.strip()
    assert content_stripped.startswith("["), (
        f"Chrome Trace file should start with '[', got: {content_stripped[:20]}"
    )
    assert content_stripped.endswith("]"), (
        f"Chrome Trace file should end with ']', got: {content_stripped[-20:]}"
    )

    # Parse and validate structure
    data: object = json.loads(content)
    assert isinstance(data, list), (
        f"Chrome Trace file should contain a JSON array, got {type(data)}"
    )

    # Validate each item is a dict (JSON object)
    for idx, item in enumerate(data):
        assert isinstance(item, dict), (
            f"Item {idx} in Chrome Trace file should be a dict, got {type(item)}"
        )

    # Cast to expected type after validation
    return data  # type: ignore[return-value]

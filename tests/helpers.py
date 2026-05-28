"""Shared helper classes and functions for gc-monitor tests."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping
from unittest.mock import Mock

from gc_monitor.exporters.exporter import EventsExporter
from gc_monitor.protocol import TGCStatsInfo, TInstantMsg
from gc_monitor.target_process import TargetProcessMetadata

# pyright: reportImplicitOverride=none


__all__ = [
    "MockHandler",
    "MockExporter",
    "MockGCMonitorThread",
    "create_mock_stats_item",
    "create_jsonl_record",
    "assert_valid_chrome_trace_format",
    "assert_is_complete",
    "assert_is_counter",
    "assert_is_process_meta",
    "assert_is_thread_meta",
]


class MockHandler:
    """Mock MonitorHandler for testing.

    This class simulates a MonitorHandler that returns predefined events
    on each read() call. It supports event-based synchronization for tests.
    """

    def __init__(self, events_per_read: list[list[TGCStatsInfo]] | None = None) -> None:
        """Initialize the mock handler.

        Args:
            events_per_read: List of event batches to return on each read() call.
        """
        self.events_per_read = events_per_read or []
        self._read_index = 0
        self._close_called = False
        self._read_count = 0
        self._read_event = threading.Event()

    def read(self) -> list[TGCStatsInfo]:
        """Read and return the next batch of events.

        Returns:
            List of GCStatsItem instances for this read call.
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


class MockExporter(EventsExporter):
    """Mock GCMonitorExporter for testing.

    This class simulates an exporter that collects events in memory.
    It supports event-based synchronization for tests.
    """

    def __init__(self, metadata: TargetProcessMetadata | None = None) -> None:
        """Initialize the mock exporter.

        Args:
            metadata: Target process metadata (defaults to {"pid": 0}).
        """
        super().__init__(metadata or {"pid": 0})
        self.events: list[TGCStatsInfo] = []
        self.instant_events: list[tuple[int, TInstantMsg]] = []
        self._close_called = False
        self._event_added = threading.Event()

    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        """Add an event to the exporter.

        Args:
            pid: Process ID.
            item: The stats item to add.
        """
        self.events.append(item)
        self._event_added.set()  # Signal that event was added

    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        """Add an instant event to the exporter.

        Args:
            pid: Process ID.
            item: The instant message to add.
        """
        self.instant_events.append((pid, item))
        self._event_added.set()

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
    ts_start: int = 1_500_000_000,
    ts_stop: int = 1_505_000_000,
    iid: int = 0,
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
) -> TGCStatsInfo:
    """Create a mock GCStatsItem with specified values.

    This is a factory function for creating GCStatsItem dicts
    with all required fields.

    Args:
        gen: GC generation (0, 1, or 2).
        ts_start: Start timestamp in nanoseconds.
        ts_stop: Stop timestamp in nanoseconds.
        iid: Interpreter ID.
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

    Returns:
        GCStatsItem dict with all fields set.
    """
    return SimpleNamespace(
        gen=gen,
        iid=iid,
        ts_start=ts_start,
        ts_stop=ts_stop,
        collections=collections,
        collected=collected,
        uncollectable=uncollectable,
        candidates=candidates,
        object_visits=object_visits,
        objects_transitively_reachable=objects_transitively_reachable,
        objects_not_transitively_reachable=objects_not_transitively_reachable,
        heap_size=heap_size,
        work_to_do=work_to_do,
        duration=duration,
    )


def create_jsonl_record(
    pid: int = 123,
    tid: int = 1,
    gen: int = 0,
    iid: int = 1,
    ts_start: int = 1_000_000,
    ts_stop: int = 2_000_000,
    heap_size: int = 1000,
    collections: int = 1,
    collected: int = 100,
    uncollectable: int = 0,
    candidates: int = 0,
    duration: float = 1.0,
) -> dict[str, int | float]:
    return {
        "pid": pid,
        "tid": tid,
        "gen": gen,
        "iid": iid,
        "ts_start": ts_start,
        "ts_stop": ts_stop,
        "heap_size": heap_size,
        "collections": collections,
        "collected": collected,
        "uncollectable": uncollectable,
        "candidates": candidates,
        "duration": duration,
    }


def assert_valid_jsonl_format(file_path: Path) -> list[dict[str, Any]]:
    """Validate that a file contains valid JSONL format (one JSON object per line).

    Args:
        file_path: Path to the JSONL file to validate.

    Returns:
        List of parsed event dictionaries.

    Raises:
        AssertionError: If the file is not valid JSONL format.
    """
    assert file_path.exists(), f"File {file_path} does not exist"

    data: list[dict[str, Any]] = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj: object = json.loads(line)
            assert isinstance(obj, dict), (
                f"Line {line_no} in JSONL file should be a JSON object, got {type(obj)}"
            )
            data.append(obj)  # type: ignore[arg-type]

    assert len(data) > 0, f"JSONL file {file_path} is empty"
    return data


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


def assert_is_complete(event: dict, **expected: Any) -> None:
    assert event["ph"] == "X"
    for key, value in expected.items():
        if key == "args":
            for arg_key, arg_value in value.items():
                assert event["args"][arg_key] == arg_value
        else:
            assert event[key] == value


def assert_is_counter(event: dict, **expected: Any) -> None:
    assert event["ph"] == "C"
    for key, value in expected.items():
        if key == "args":
            for arg_key, arg_value in value.items():
                assert event["args"][arg_key] == arg_value
        else:
            assert event[key] == value


def assert_is_process_meta(event: dict, **expected: Any) -> None:
    assert event["ph"] == "M"
    assert event["name"] == "process_name"
    for key, value in expected.items():
        if key == "args":
            for arg_key, arg_value in value.items():
                assert event["args"][arg_key] == arg_value
        else:
            assert event[key] == value


def assert_is_thread_meta(event: dict, **expected: Any) -> None:
    assert event["ph"] == "M"
    assert event["name"] == "thread_name"
    for key, value in expected.items():
        if key == "args":
            for arg_key, arg_value in value.items():
                assert event["args"][arg_key] == arg_value
        else:
            assert event[key] == value


def assert_is_instant_event(event: dict, **expected: Mapping[str, str|int]) -> None:
    assert event["ph"] == "I"
    assert event["s"] == "p"

    for key, value in expected.items():
        assert event[key] == value

def assert_is_instant_msg(msg: dict[str, Any], **expected: Mapping[str, str|int]) -> None:
    assert msg["type"] == "i"

    for key, value in expected.items():
        assert msg[key] == value

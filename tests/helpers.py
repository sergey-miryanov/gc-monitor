"""Shared helper classes and functions for gc-monitor tests."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Mapping

from gc_monitor.data import GCStatsInfo, IncrementalGCStatsInfo
from gc_monitor.exporters.exporter import EventsExporter
from gc_monitor.protocol import TGCStatsInfo, TInstantMsg

# pyright: reportImplicitOverride=none


__all__ = [
    "MockExporter",
    "create_mock_stats_item",
    "create_mock_incremental_item",
    "create_jsonl_record",
    "assert_valid_chrome_trace_format",
    "assert_is_complete",
    "assert_is_counter",
    "assert_is_process_meta",
    "assert_is_thread_meta",
    "assert_is_instant_event",
    "assert_is_instant_msg",
]


class MockExporter(EventsExporter):
    """Mock GCMonitorExporter for testing.

    This class simulates an exporter that collects events in memory.
    It supports event-based synchronization for tests.
    """

    def __init__(self) -> None:
        """Initialize the mock exporter."""
        super().__init__()
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


def create_mock_stats_item(
    gen: int = 0,
    ts_start: int = 1_500_000_000,
    ts_stop: int = 1_505_000_000,
    iid: int = 0,
    collections: int = 50,
    collected: int = 200,
    uncollectable: int = 10,
    candidates: int = 40,
    heap_size: int = 52428800,
    duration: float = 0.005,
) -> GCStatsInfo:
    return GCStatsInfo(
        gen=gen,
        iid=iid,
        ts_start=ts_start,
        ts_stop=ts_stop,
        heap_size=heap_size,
        collections=collections,
        collected=collected,
        uncollectable=uncollectable,
        candidates=candidates,
        duration=duration,
    )


def create_mock_incremental_item(**kwargs: object) -> IncrementalGCStatsInfo:
    defaults: dict[str, object] = dict(
        gen=0, iid=0, ts_start=1_500_000_000, ts_stop=1_505_000_000,
        heap_size=52428800, collections=50, collected=200, uncollectable=10,
        candidates=40, duration=0.005,
        increment_size=1000, alive_size=800,
        ts_mark_alive_start=1_500_000_000, ts_mark_alive_stop=1_501_000_000,
        ts_fill_increment_start=1_501_000_000, ts_fill_increment_stop=1_502_000_000,
        ts_deduce_unreachable_start=1_502_000_000, ts_deduce_unreachable_stop=1_503_000_000,
    )
    defaults.update(kwargs)
    return IncrementalGCStatsInfo(**defaults)  # type: ignore[arg-type]


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

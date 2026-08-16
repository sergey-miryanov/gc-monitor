from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Set
from pathlib import Path
from typing import override

from gcmon.data import GCStatsInfo, GenLoss, LossMsg
from gcmon.exporters.exporter import EventsExporter
from gcmon.protocol import TGCStatsInfo, TInstantMsg, TLossMsg

_JsonValue = int | float | str
ChromeTraceValue = _JsonValue | Mapping[str, _JsonValue]
JsonlRecord = dict[str, _JsonValue]
DefaultsValue = Path | float | None | int | str | bool

__all__ = [
    "ChromeTraceValue",
    "DefaultsValue",
    "JsonlRecord",
    "MockExporter",
    "assert_is_begin",
    "assert_is_counter",
    "assert_is_end",
    "assert_is_instant_event",
    "assert_is_instant_msg",
    "assert_is_process_meta",
    "assert_is_thread_meta",
    "assert_valid_chrome_trace_format",
    "create_jsonl_record",
    "create_mock_incremental_item",
    "create_mock_loss_item",
    "create_mock_stats_item",
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
        # Which pid each record was exported for, and every loss record. The
        # defect class spec 0038 is about -- per-pid state surviving a process
        # it does not belong to -- produces no records of its own; it shows up
        # as the wrong pid's cursor answering, or as a loss window for
        # collections that never happened. Neither is visible in `events`.
        self.events_by_pid: dict[int, list[TGCStatsInfo]] = {}
        self.loss_events: list[tuple[int, TLossMsg]] = []
        # One entry per tick that observed anything, which is the only
        # evidence a process gcmon never saw collect existed at all (ADR-0011).
        self.liveness: list[tuple[Set[int], int]] = []
        self._close_called = False
        self._event_added = threading.Event()

    @override
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        """Add an event to the exporter.

        Args:
            pid: Process ID.
            item: The stats item to add.
        """
        self.events.append(item)
        self.events_by_pid.setdefault(pid, []).append(item)
        self._event_added.set()  # Signal that event was added

    @override
    def add_loss_event(self, pid: int, item: TLossMsg) -> None:
        """Record a loss window the monitor's arithmetic produced."""
        self.loss_events.append((pid, item))
        self._event_added.set()

    @override
    def add_process_liveness(self, pids: Set[int], ts_ns: int) -> None:
        """Record one tick's liveness observation."""
        self.liveness.append((pids, ts_ns))

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        """Add an instant event to the exporter.

        Args:
            pid: Process ID.
            item: The instant message to add.
        """
        self.instant_events.append((pid, item))
        self._event_added.set()

    @override
    def close(self) -> None:
        """Close the exporter."""
        self._close_called = True

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


def create_mock_loss_item(
    iid: int = 0,
    ts_start: int = 1_000,
    ts_stop: int = 2_000,
    gen: int = 0,
    observed_count: int = 0,
    lost_count: int = 1,
    lost_pause_ns: int = 0,
    lost_from: int = 0,
) -> LossMsg:
    """A loss record naming one generation, for tests about everything else.

    A real record carries an entry per generation active in the interval;
    tests that care about that build their own ``gens``.
    """
    return LossMsg(
        iid=iid,
        ts_start=ts_start,
        ts_stop=ts_stop,
        gens=[
            GenLoss(
                gen=gen,
                observed_count=observed_count,
                lost_count=lost_count,
                lost_pause_ns=lost_pause_ns,
                lost_from=lost_from,
            )
        ],
    )


def create_mock_incremental_item(
    gen: int = 0,
    iid: int = 0,
    ts_start: int = 1_500_000_000,
    ts_stop: int = 1_505_000_000,
    heap_size: int = 52428800,
    collections: int = 50,
    collected: int = 200,
    uncollectable: int = 10,
    candidates: int = 40,
    duration: float = 0.005,
    increment_size: int | None = 1000,
    alive_size: int | None = 800,
    ts_mark_alive_start: int | None = 1_500_000_000,
    ts_mark_alive_stop: int | None = 1_501_000_000,
    ts_fill_increment_start: int | None = 1_501_000_000,
    ts_fill_increment_stop: int | None = 1_502_000_000,
    ts_deduce_unreachable_start: int | None = 1_502_000_000,
    ts_deduce_unreachable_stop: int | None = 1_503_000_000,
    ts_handle_weakref_callbacks_start: int | None = 1_503_000_000,
    ts_handle_weakref_callbacks_stop: int | None = 1_504_000_000,
    ts_finalize_garbage_stop: int | None = 1_505_000_000,
    finalized_garbage_count: int | None = 42,
    ts_handle_resurrected_stop: int | None = 1_506_000_000,
    ts_clear_weakrefs_stop: int | None = 1_507_000_000,
    clear_weakrefs_count: int | None = 7,
    ts_delete_garbage_start: int | None = 1_508_000_000,
    ts_delete_garbage_stop: int | None = 1_509_000_000,
    deleted_garbage_count: int | None = 13,
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
        increment_size=increment_size,
        alive_size=alive_size,
        ts_mark_alive_start=ts_mark_alive_start,
        ts_mark_alive_stop=ts_mark_alive_stop,
        ts_fill_increment_start=ts_fill_increment_start,
        ts_fill_increment_stop=ts_fill_increment_stop,
        ts_deduce_unreachable_start=ts_deduce_unreachable_start,
        ts_deduce_unreachable_stop=ts_deduce_unreachable_stop,
        ts_handle_weakref_callbacks_start=ts_handle_weakref_callbacks_start,
        ts_handle_weakref_callbacks_stop=ts_handle_weakref_callbacks_stop,
        ts_finalize_garbage_stop=ts_finalize_garbage_stop,
        finalized_garbage_count=finalized_garbage_count,
        ts_handle_resurrected_stop=ts_handle_resurrected_stop,
        ts_clear_weakrefs_stop=ts_clear_weakrefs_stop,
        clear_weakrefs_count=clear_weakrefs_count,
        ts_delete_garbage_start=ts_delete_garbage_start,
        ts_delete_garbage_stop=ts_delete_garbage_stop,
        deleted_garbage_count=deleted_garbage_count,
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


def assert_valid_jsonl_format(file_path: Path) -> list[JsonlRecord]:
    """Validate that a file contains valid JSONL format (one JSON object per line).

    Args:
        file_path: Path to the JSONL file to validate.

    Returns:
        List of parsed event dictionaries.

    Raises:
        AssertionError: If the file is not valid JSONL format.
    """
    assert file_path.exists(), f"File {file_path} does not exist"

    data: list[JsonlRecord] = []
    with open(file_path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            assert isinstance(obj, dict), f"Line {line_no} in JSONL file should be a JSON object, got {type(obj)}"
            data.append(obj)

    assert len(data) > 0, f"JSONL file {file_path} is empty"
    return data


def assert_valid_chrome_trace_format(file_path: Path) -> list[dict[str, ChromeTraceValue]]:
    """Validate that a file contains valid Chrome Trace format (JSON array of objects).

    Args:
        file_path: Path to the JSON file to validate.

    Returns:
        List of parsed event dictionaries.

    Raises:
        AssertionError: If the file is not valid Chrome Trace format.
    """
    assert file_path.exists(), f"File {file_path} does not exist"

    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    # Check basic JSON array structure
    content_stripped = content.strip()
    assert content_stripped.startswith("["), f"Chrome Trace file should start with '[', got: {content_stripped[:20]}"
    assert content_stripped.endswith("]"), f"Chrome Trace file should end with ']', got: {content_stripped[-20:]}"

    # Parse and validate structure
    data = json.loads(content)
    assert isinstance(data, list), f"Chrome Trace file should contain a JSON array, got {type(data)}"

    # Validate each item is a dict (JSON object)
    for idx, item in enumerate(data):
        assert isinstance(item, dict), f"Item {idx} in Chrome Trace file should be a dict, got {type(item)}"

    return data


def assert_is_begin(event: dict[str, ChromeTraceValue], **expected: ChromeTraceValue) -> None:
    assert event["ph"] == "B"
    for key, value in expected.items():
        if key == "args":
            assert isinstance(value, Mapping)
            args = event["args"]
            assert isinstance(args, Mapping)
            for arg_key, arg_value in value.items():
                assert args[arg_key] == arg_value
        else:
            assert event[key] == value


def assert_is_end(event: dict[str, ChromeTraceValue], **expected: ChromeTraceValue) -> None:
    assert event["ph"] == "E"
    for key, value in expected.items():
        if key == "args":
            assert isinstance(value, Mapping)
            args = event["args"]
            assert isinstance(args, Mapping)
            for arg_key, arg_value in value.items():
                assert args[arg_key] == arg_value
        else:
            assert event[key] == value


def assert_is_counter(event: dict[str, ChromeTraceValue], **expected: ChromeTraceValue) -> None:
    assert event["ph"] == "C"
    for key, value in expected.items():
        if key == "args":
            assert isinstance(value, Mapping)
            args = event["args"]
            assert isinstance(args, Mapping)
            for arg_key, arg_value in value.items():
                assert args[arg_key] == arg_value
        else:
            assert event[key] == value


def assert_is_process_meta(event: dict[str, ChromeTraceValue], **expected: ChromeTraceValue) -> None:
    assert event["ph"] == "M"
    assert event["name"] == "process_name"
    for key, value in expected.items():
        if key == "args":
            assert isinstance(value, Mapping)
            args = event["args"]
            assert isinstance(args, Mapping)
            for arg_key, arg_value in value.items():
                assert args[arg_key] == arg_value
        else:
            assert event[key] == value


def assert_is_thread_meta(event: dict[str, ChromeTraceValue], **expected: ChromeTraceValue) -> None:
    assert event["ph"] == "M"
    assert event["name"] == "thread_name"
    for key, value in expected.items():
        if key == "args":
            assert isinstance(value, Mapping)
            args = event["args"]
            assert isinstance(args, Mapping)
            for arg_key, arg_value in value.items():
                assert args[arg_key] == arg_value
        else:
            assert event[key] == value


def assert_is_instant_event(event: dict[str, ChromeTraceValue], **expected: str | int) -> None:
    assert event["ph"] == "I"
    assert event["s"] == "p"

    for key, value in expected.items():
        assert event[key] == value


def assert_is_instant_msg(msg: JsonlRecord, **expected: str | int) -> None:
    assert msg["type"] == "i"

    for key, value in expected.items():
        assert msg[key] == value

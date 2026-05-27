"""Chrome Trace Event format exporter for GC monitoring data."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import override

from ..data import ts_to_us
from ..lock_strategy import LockStrategy
from ..protocol import TGCStatsInfo, TIncrementalGCStatsInfo, TInstantMsg
from ..target_process import TargetProcessMetadata
from .chrome_trace_format import (
    InstantEvent,
    TraceEvent,
    convert_item_to_trace_format,
    instant_event,
    process_meta,
    thread_meta,
)
from .exporter import EventsExporter

__all__ = [
    "TraceExporter",
]


class TraceExporter(EventsExporter):
    """
    Exporter for Chrome Trace Event format.

    Collects GC monitoring events and exports them as JSON
    compatible with Chrome DevTools Performance panel.
    """

    def __init__(
        self,
        lock: type[LockStrategy],
        metadata: TargetProcessMetadata,
        output_path: Path,
        flush_threshold: int = 1000,
    ) -> None:
        super().__init__(metadata)
        self._lock = lock()
        self._events: list[TraceEvent] = []
        self._control_events: list[InstantEvent] = []
        self._control_lock: LockStrategy = lock()
        self._flush_threshold = flush_threshold
        self._output_path = output_path
        self._closed = False
        self._events_count = 0
        self._written_count = 0
        self._tids: set[tuple[int, int]] = set()
        self._pids: set[int] = {metadata["pid"],}

        self._write_begin_marker()

    @override
    def add_event(self, pid: int, item: TGCStatsInfo | TIncrementalGCStatsInfo) -> None:
        self._tids.add((pid, item.iid))
        self._pids.add(pid)

        events = convert_item_to_trace_format(pid, item)

        events_to_flush = []
        with self._lock.lock():
            self._events_count += 1
            self._events.extend(events)

            if len(self._events) >= self._flush_threshold:
                events_to_flush = self._events[:]
                self._events.clear()

        self._flush(events_to_flush)

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        event = instant_event(pid, item.name, ts_to_us(item.ts))

        events: list[InstantEvent] = []
        with self._control_lock.lock():
            self._control_events.append(event)
            events = self._control_events[:]
            self._control_events.clear()

        self._flush(events)

    def _flush(self, events: Sequence[TraceEvent]) -> None:
        if events:
            self._write_to_file(list(events))

    def _write_to_file(self, events: list[TraceEvent]) -> None:
        with open(self._output_path, "a", encoding="utf-8") as f:
            linesep = "\n"
            f.writelines(f",{linesep}{json.dumps(e)}" for e in events)
            f.flush()

    def _write_metadata(self) -> None:
        with open(self._output_path, "a", encoding="utf-8") as f:
            linesep = "\n"

            for pid in self._pids:
                if pid != self._metadata["pid"]:
                    f.write(f",{linesep}{json.dumps(process_meta(pid, 'Child Process'))}")

            for pid, tid in self._tids:
                f.write(f",{linesep}{json.dumps(thread_meta(pid, tid, f'Thread {tid}'))}")

    def _write_begin_marker(self) -> None:
        pid = self._metadata["pid"]
        with open(self._output_path, "w", encoding="utf-8") as f:
            linesep = "\n"
            f.write(f"[{linesep}{json.dumps(process_meta(pid, 'Parent Process'))}")

    def _write_finish_marker(self) -> None:
        with open(self._output_path, "a", encoding="utf-8") as f:
            linesep = "\n"
            f.write(f"{linesep}]{linesep}")

    @override
    def close(self) -> None:
        """
        Close the exporter and write all events to file.

        Safe to call multiple times - only the first call writes the file.
        """
        if self._closed:
            return

        events: list[TraceEvent] = []
        control_events: list[InstantEvent] = []
        with self._lock.lock():
            events = self._events[:]
            self._events.clear()
        with self._control_lock.lock():
            control_events = self._control_events[:]
            self._control_events.clear()

        self._flush(events)
        self._flush(control_events)
        self._write_metadata()
        self._write_finish_marker()
        self._closed = True

    @override
    def get_event_count(self) -> int:
        """Return the number of collected events."""
        return self._events_count

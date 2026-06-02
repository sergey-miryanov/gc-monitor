"""Chrome Trace Event format exporter for GC monitoring data."""

import json
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import override

from ..data import ts_to_us
from ..protocol import TGCStatsInfo, TIncrementalGCStatsInfo, TInstantMsg
from .chrome_trace_format import (
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
        output_path: Path,
        flush_threshold: int = 1000,
    ) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._events: list[TraceEvent] = []
        self._flush_threshold = flush_threshold
        self._output_path = output_path
        self._closed = False
        self._events_count = 0
        self._written_count = 0
        self._tids: set[tuple[int, int]] = set()
        self._pids: set[int] = set()
        self._has_written = False

    def _add_events(self, events: list[TraceEvent], count: int = 0) -> None:
        events_to_flush = []
        with self._lock:
            self._events_count += count
            self._events.extend(events)
            if len(self._events) >= self._flush_threshold:
                events_to_flush = self._events[:]
                self._events.clear()
        if events_to_flush:
            with self._io_lock:
                self._flush(events_to_flush)

    @override
    def add_event(self, pid: int, item: TGCStatsInfo | TIncrementalGCStatsInfo) -> None:
        meta_events: list[TraceEvent] = []
        with self._lock:
            if pid not in self._pids:
                self._pids.add(pid)
                meta_events.append(process_meta(pid, f"Process {pid}"))
            if (pid, item.iid) not in self._tids:
                self._tids.add((pid, item.iid))
                meta_events.append(thread_meta(pid, item.iid, f"Thread {item.iid}"))
        self._add_events(meta_events + convert_item_to_trace_format(pid, item), count=1)

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        meta_events: list[TraceEvent] = []
        with self._lock:
            if pid not in self._pids:
                self._pids.add(pid)
                meta_events.append(process_meta(pid, f"Process {pid}"))
        self._add_events(meta_events + [instant_event(pid, item.name, ts_to_us(item.ts))])

    def _flush(self, events: Sequence[TraceEvent]) -> None:
        if events:
            self._write_to_file(list(events))

    def _write_to_file(self, events: list[TraceEvent]) -> None:
        with open(self._output_path, "a", encoding="utf-8") as f:
            linesep = "\n"
            if not self._has_written:
                self._has_written = True
                f.write(f"[{linesep}{json.dumps(events[0])}")
                events = events[1:]
            for e in events:
                f.write(f",{linesep}{json.dumps(e)}")
            f.flush()

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
        with self._lock:
            if self._closed:
                return
            self._closed = True
            events = self._events[:]
            self._events.clear()

        with self._io_lock:
            self._flush(events)

            if not self._has_written:
                with open(self._output_path, "w", encoding="utf-8") as f:
                    f.write("[]\n")
            else:
                self._write_finish_marker()

    @override
    def get_event_count(self) -> int:
        """Return the number of collected events."""
        return self._events_count

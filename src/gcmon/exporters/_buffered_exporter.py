"""Base class for trace event exporters."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import override

from ..protocol import TGCStatsInfo, TInstantMsg
from ..trace_event import TraceEvent, instant_event, process_meta, thread_meta
from .encoder import EventEncoder
from .exporter import EventsExporter
from .trace_converter import convert_item_to_trace_format

__all__ = ["BufferedTraceExporter"]


class BufferedTraceExporter(EventsExporter):
    """Base class for trace exporters."""

    def __init__(
        self,
        encoder: EventEncoder,
        output_path: Path,
        flush_threshold: int = 1000,
    ) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._buffer: list[TraceEvent] = []
        self._flush_threshold = flush_threshold
        self._output_path = output_path
        self._encoder = encoder
        self._seen_pids: set[int] = set()
        self._seen_tids: set[tuple[int, int]] = set()
        self._closed = False
        self._encoder.open(output_path)

    def _enqueue(self, events: list[TraceEvent]) -> None:
        to_write: list[TraceEvent] = []
        with self._lock:
            self._buffer.extend(events)
            if len(self._buffer) >= self._flush_threshold:
                to_write = self._buffer[:]
                self._buffer.clear()
        if to_write:
            with self._io_lock:
                self._encoder.write_events(to_write)

    def _build_meta(self, pid: int, iid: int | None) -> list[TraceEvent]:
        """Emit ``ProcessMeta`` / ``ThreadMeta`` for any pid/iid pair that
        has not been seen yet."""
        meta: list[TraceEvent] = []
        with self._lock:
            if pid not in self._seen_pids:
                self._seen_pids.add(pid)
                meta.append(process_meta(pid, f"Process {pid}"))
            if iid is not None and (pid, iid) not in self._seen_tids:
                self._seen_tids.add((pid, iid))
                meta.append(thread_meta(pid, iid, f"Thread {iid}"))
        return meta

    @override
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        events = [*self._build_meta(pid, item.iid), *convert_item_to_trace_format(pid, item)]
        self._enqueue(events)

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        events = [*self._build_meta(pid, None), instant_event(pid, item.name, item.ts)]
        self._enqueue(events)

    @override
    def close(self) -> None:
        """Drain the buffer and close the encoder."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            remaining = self._buffer[:]
            self._buffer.clear()
        with self._io_lock:
            if remaining:
                self._encoder.write_events(remaining)
            self._encoder.close()

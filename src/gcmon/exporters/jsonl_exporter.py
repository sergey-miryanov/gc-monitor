"""JSONL file exporter for GC monitoring data.

Exports GC events to a file in JSONL format (one JSON object per line).
"""

import json
import threading
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TextIO, override

from ..poll_status import ProcessLifecycle
from ..protocol import TGCStatsInfo, TInstantMsg, to_mapping
from .exporter import EventsExporter

__all__ = ["JsonlExporter"]


class JsonlExporter(EventsExporter):
    """
    Exporter that writes GC events to one JSON object per line.

    Output goes to the destination provided by _open_writer() (file or stdout).
    Events are buffered in memory and flushed when the buffer reaches
    flush_threshold events.
    """

    def __init__(
        self,
        output_path: Path | None = None,
        flush_threshold: int = 100,
    ) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._flush_threshold = flush_threshold
        self._events: list[dict[str, str | int | float]] = []
        self._output_path = output_path

    @override
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        event: dict[str, str | int | float] = {
            "pid": pid,
            "tid": item.iid,
        }
        event.update(to_mapping(item))

        events: list[dict[str, str | int | float]] = []
        with self._lock:
            self._events.append(event)

            if len(self._events) >= self._flush_threshold:
                events = self._events[:]
                self._events.clear()

        if events:
            with self._io_lock:
                self._flush(events)

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        event: dict[str, str | int | float] = {
            "pid": pid,
        }
        event.update(to_mapping(item))

        events: list[dict[str, str | int | float]] = []
        with self._lock:
            self._events.append(event)

            if len(self._events) >= self._flush_threshold:
                events = self._events[:]
                self._events.clear()

        if events:
            with self._io_lock:
                self._flush(events)

    @override
    def mark_process_lifecycle(
        self, pid: int, kind: ProcessLifecycle, ts_ns: int,
    ) -> None:
        """No-op: JSONL has no per-process lifetime track concept."""

    def _flush(self, events: list[dict[str, str|int|float]]) -> None:
        if not events:
            return
        with self._open_writer() as w:
            for event in events:
                w.write(json.dumps(event) + "\n")
            w.flush()

    def _open_writer(self) -> AbstractContextManager[TextIO]:
        assert self._output_path is not None
        return open(self._output_path, "a", encoding="utf-8")

    @override
    def close(self) -> None:
        with self._lock:
            events = self._events
            self._events = []

        if events:
            with self._io_lock:
                self._flush(events)



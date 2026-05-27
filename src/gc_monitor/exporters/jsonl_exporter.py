"""JSONL file exporter for GC monitoring data.

Exports GC events to a file in JSONL format (one JSON object per line).
"""

import json
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TextIO, override

from ..lock_strategy import LockStrategy
from ..protocol import TGCStatsInfo, TIncrementalGCStatsInfo, TInstantMsg, to_mapping
from ..target_process import TargetProcessMetadata
from .exporter import EventsExporter

__all__ = ["JsonlExporter"]


class JsonlExporter(EventsExporter):
    """
    Exporter that writes GC events to one JSON object per line.

    Output goes to the destination provided by _open_writer() (file or stdout).
    Events are buffered in memory and flushed when the buffer reaches
    flush_threshold events.

    Thread safety is ensured via a LockStrategy instance passed at construction.
    """

    def __init__(
        self,
        lock: type[LockStrategy],
        metadata: TargetProcessMetadata,
        output_path: Path | None = None,
        flush_threshold: int = 100,
    ) -> None:
        super().__init__(metadata)
        self._lock = lock()
        self._flush_threshold = flush_threshold
        self._event_count = 0
        self._events: list[dict[str, str | int | float]] = []
        self._output_path = output_path

    @override
    def add_event(self, pid: int, item: TGCStatsInfo | TIncrementalGCStatsInfo) -> None:
        event: dict[str, str | int | float] = {
            "pid": pid,
            "tid": item.iid,
        }
        event.update(to_mapping(item))

        events: list[dict[str, str | int | float]] = []
        with self._lock.lock():
            self._events.append(event)
            self._event_count += 1
            if len(self._events) >= self._flush_threshold:
                events = self._events[:]
                self._events.clear()

        self._flush(events)

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        event: dict[str, str | int | float] = {
            "pid": pid,
        }
        event.update(to_mapping(item))

        events: list[dict[str, str | int | float]] = []
        with self._lock.lock():
            self._events.append(event)
            self._event_count += 1
            if len(self._events) >= self._flush_threshold:
                events = self._events[:]
                self._events.clear()

        self._flush(events)

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
        events = self._events
        self._events = []
        self._flush(events)

    @override
    def get_event_count(self) -> int:
        return self._event_count

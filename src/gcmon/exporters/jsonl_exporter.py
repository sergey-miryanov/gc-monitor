"""JSONL file exporter for GC monitoring data."""

import json
import threading
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TextIO, override

from ..model.protocol import JsonlRecord, TGCStatsInfo, TInstantMsg, TLossMsg, to_mapping
from .exporter import EventsExporter

__all__ = ["JsonlExporter"]


class JsonlExporter(EventsExporter):
    """Write GC events as one JSON object per line.

    ``StdoutExporter`` subclasses this and sends the same lines to stdout
    by overriding ``_open_writer``.
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
        self._events: list[JsonlRecord] = []
        self._output_path = output_path

    @override
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        event: JsonlRecord = {
            "pid": pid,
        }
        event.update(to_mapping(item))

        events: list[JsonlRecord] = []
        with self._lock:
            self._events.append(event)

            if len(self._events) >= self._flush_threshold:
                events = self._events[:]
                self._events.clear()

        if events:
            with self._io_lock:
                self._flush(events)

    @override
    def add_loss_event(self, pid: int, item: TLossMsg) -> None:
        event: JsonlRecord = {
            "pid": pid,
        }
        event.update(to_mapping(item))

        events: list[JsonlRecord] = []
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
        event: JsonlRecord = {
            "pid": pid,
        }
        event.update(to_mapping(item))

        events: list[JsonlRecord] = []
        with self._lock:
            self._events.append(event)

            if len(self._events) >= self._flush_threshold:
                events = self._events[:]
                self._events.clear()

        if events:
            with self._io_lock:
                self._flush(events)

    def _flush(self, events: list[JsonlRecord]) -> None:
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

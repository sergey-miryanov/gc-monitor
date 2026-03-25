"""JSONL file exporter for GC monitoring data.

Exports GC events to a file in JSONL format (one JSON object per line).
"""

import json
from pathlib import Path
from typing import Any, override

from .exporter import GCMonitorExporter
from .protocol import StatsItem

__all__ = ["JsonlExporter"]


class JsonlExporter(GCMonitorExporter):
    """
    Exporter that writes GC events to a file, one JSON object per line.

    Each event is written as a single line of JSON (JSONL/NDJSON format),
    making it easy to process with line-oriented tools and log aggregators.

    Events are buffered in memory and flushed to file when the buffer
    reaches flush_threshold events. This improves performance by reducing
    I/O operations.
    """

    def __init__(
        self,
        pid: int,
        output_path: Path,
        thread_id: int = 0,
        flush_threshold: int = 100,
    ) -> None:
        """
        Initialize the JSONL file exporter.

        Args:
            pid: Process ID being monitored
            output_path: Path to output file for JSONL data
            thread_id: Thread ID for events (default: 0)
            flush_threshold: Number of events to buffer before flushing to file (default: 100)
        """
        super().__init__(pid, thread_id=thread_id)
        self._output_path = output_path
        self._flush_threshold = flush_threshold
        self._event_count = 0
        self._events: list[dict[str, Any]] = []

    @override
    def add_event(self, stats_item: StatsItem) -> None:
        """
        Add a GC event to the buffer.

        Events are buffered in memory and flushed to file when the buffer
        reaches flush_threshold events. This improves performance by reducing
        I/O operations.

        Args:
            stats_item: StatsItem instance from callback
        """
        event: dict[str, Any] = {
            "pid": self._pid,
            "tid": self._thread_id,
            "gen": stats_item.gen,
            "ts":stats_item.ts,
            "collections": stats_item.collections,
            "collected": stats_item.collected,
            "uncollectable": stats_item.uncollectable,
            "candidates": stats_item.candidates,
            "object_visits": stats_item.object_visits,
            "objects_transitively_reachable": stats_item.objects_transitively_reachable,
            "objects_not_transitively_reachable": stats_item.objects_not_transitively_reachable,
            "heap_size": stats_item.heap_size,
            "work_to_do": stats_item.work_to_do,
            "duration": stats_item.duration,
            "total_duration": stats_item.total_duration,
        }

        self._events.append(event)
        self._event_count += 1

        # Auto-flush if threshold reached
        if len(self._events) >= self._flush_threshold:
            self._flush()

    def _flush(self) -> None:
        """Flush buffered events to file."""
        if not self._events:
            return

        with open(self._output_path, "a", encoding="utf-8") as file:
            for event in self._events:
                file.write(json.dumps(event) + "\n")
            file.flush()
        self._events.clear()

    @override
    def close(self) -> None:
        """
        Close the exporter and write all remaining events to file.

        Safe to call multiple times - only the first call closes the file.
        """
        # Flush any remaining buffered events
        self._flush()

    def get_event_count(self) -> int:
        """Return the number of exported events (buffered + flushed)."""
        return self._event_count

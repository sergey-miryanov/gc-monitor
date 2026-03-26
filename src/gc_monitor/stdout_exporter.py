"""Stdout exporter for GC monitoring data.

Exports GC events to stdout in a one-line-per-item format (JSONL/NDJSON).
"""

import json
import sys
from typing import Any, override

from .exporter import GCMonitorExporter
from .protocol import StatsItem

__all__ = ["StdoutExporter"]


class StdoutExporter(GCMonitorExporter):
    """
    Exporter that writes GC events to stdout, one JSON object per line.

    Each event is written as a single line of JSON (JSONL/NDJSON format),
    making it easy to pipe to log aggregators or processing tools.
    """

    def __init__(
        self,
        pid: int,
        thread_name: str = "GC Monitor",
        flush: bool = True,
    ) -> None:
        """
        Initialize the stdout exporter.

        Args:
            pid: Process ID being monitored
            thread_name: Name for the GC monitor thread
            flush: Whether to flush stdout after each event (default: True)
        """
        super().__init__(pid, thread_name)
        self._flush = flush
        self._event_count = 0

    @override
    def add_event(self, stats_item: StatsItem) -> None:
        """
        Write a GC event to stdout as a single JSON line.

        Args:
            stats_item: StatsItem instance from callback
        """
        event: dict[str, Any] = {
            "pid": self._pid,
            "tid": self._thread_name,
            "gen": stats_item.gen,
            "ts": stats_item.ts,
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

        # Write as single JSON line
        print(json.dumps(event), flush=self._flush)
        self._event_count += 1

    @override
    def close(self) -> None:
        """Flush stdout on close."""
        if self._flush:
            sys.stdout.flush()

    @override
    def get_event_count(self) -> int:
        """Return the number of exported events."""
        return self._event_count

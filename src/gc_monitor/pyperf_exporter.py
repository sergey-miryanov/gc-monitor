"""Pyperf JSON format exporter for GC monitoring data."""

import json
from pathlib import Path
from typing import Any, Dict, List

from ._gc_monitor import GCMonitorStatsItem
from .exporter import GCMonitorExporter


class PyperfExporter(GCMonitorExporter):
    """
    Exporter that collects events for pyperf JSON format.

    Collects GC monitoring events and exports them as JSON
    compatible with pyperf hook metadata format.
    """

    def __init__(self, pid: int) -> None:
        super().__init__(pid, thread_name="GC Monitor")
        self._events: List[Dict[str, Any]] = []
        self._start_time: float = 0.0
        self._end_time: float = 0.0

    def add_event(self, stats_item: GCMonitorStatsItem) -> None:  # pyright: ignore[reportImplicitOverride]
        """Add a GC event."""
        if not self._events:
            self._start_time = stats_item.ts
        self._end_time = stats_item.ts
        self._events.append(
            {
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
        )

    def close(self) -> None:  # pyright: ignore[reportImplicitOverride]
        """No-op for this exporter."""
        pass

    def write(self, output_path: Path) -> None:
        """Write collected events to JSON file in pyperf format."""
        data: Dict[str, Any] = {
            "version": "1.0",
            "pid": self._pid,
            "start_time": self._start_time,
            "end_time": self._end_time,
            "events": self._events,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get_event_count(self) -> int:
        """Return the number of collected events."""
        return len(self._events)

"""Chrome Trace Event format exporter for GC monitoring data."""

import json
import threading
from pathlib import Path
from typing import Any, Dict, List

from ._gc_monitor import GCMonitorStatsItem
from .exporter import GCMonitorExporter


class TraceExporter(GCMonitorExporter):
    """
    Thread-safe exporter for Chrome Trace Event format.

    Collects GC monitoring events and exports them as JSON
    compatible with Chrome DevTools Performance panel.
    """

    def __init__(self, pid: int, thread_name: str = "GC Monitor") -> None:
        """
        Initialize the trace exporter.

        Args:
            pid: Process ID being monitored
            thread_name: Name for the GC monitor thread in trace
        """
        super().__init__(pid, thread_name)
        self._events: List[Dict[str, Any]] = []
        self._metadata_added = False
        self._lock = threading.Lock()

    def add_event(self, stats_item: GCMonitorStatsItem) -> None:
        """
        Add a GC monitoring event from GCMonitorStatsItem.

        Args:
            stats_item: GCMonitorStatsItem instance from callback
        """
        # Convert timestamp to microseconds
        ts_us = int(stats_item.ts * 1_000_000)
        dur_us = int(stats_item.duration * 1_000_000)

        with self._lock:
            # Complete event for GC pause visualization
            self._events.append(
                {
                    "name": f"GC Pause (Gen {stats_item.gen})",
                    "cat": "gc",
                    "ph": "X",
                    "ts": ts_us,
                    "dur": dur_us,
                    "pid": self._pid,
                    "tid": self._thread_name,
                    "args": {
                        "generation": stats_item.gen,
                        "collected": stats_item.collected,
                        "uncollectable": stats_item.uncollectable,
                        "candidates": stats_item.candidates,
                        "object_visits": stats_item.object_visits,
                        "objects_transitively_reachable": stats_item.objects_transitively_reachable,
                        "objects_not_transitively_reachable": stats_item.objects_not_transitively_reachable,
                        "work_to_do": stats_item.work_to_do,
                        "total_duration": stats_item.total_duration,
                    },
                }
            )

            # Counter event for memory metrics
            self._events.append(
                {
                    "name": "Memory Counters",
                    "cat": "gc.memory",
                    "ph": "C",
                    "ts": ts_us,
                    "pid": self._pid,
                    "tid": self._thread_name,
                    "args": {
                        "heap_size": stats_item.heap_size,
                        "collected": stats_item.collected,
                        "uncollectable": stats_item.uncollectable,
                        "candidates": stats_item.candidates,
                        "collections": stats_item.collections,
                    },
                }
            )

    def _add_metadata(self) -> None:
        """Add process and thread metadata events."""
        # Process name
        self._events.append(
            {
                "name": "process_name",
                "ph": "M",
                "pid": self._pid,
                "tid": self._thread_name,
                "args": {"name": f"Python Process (PID: {self._pid})"},
            }
        )

        # Thread name
        self._events.append(
            {
                "name": "thread_name",
                "ph": "M",
                "pid": self._pid,
                "tid": self._thread_name,
                "args": {"name": "GC Monitor"},
            }
        )

    def save_json(self, output_path: Path) -> None:
        """
        Save collected events to JSON file.

        Args:
            output_path: Path to output JSON file

        Note:
            Automatically adds metadata if not already added.
        """
        with self._lock:
            if not self._metadata_added:
                self._add_metadata()
                self._metadata_added = True

            # Copy events to avoid modification during save
            trace_data = list(self._events)

        output_path = Path(output_path)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(trace_data, f, indent=2)

    def clear(self) -> None:
        """Clear all collected events."""
        with self._lock:
            self._events.clear()
            self._metadata_added = False

    def get_event_count(self) -> int:
        """Return the number of collected events."""
        with self._lock:
            return len(self._events)

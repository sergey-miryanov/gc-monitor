"""Chrome Trace Event format exporter for GC monitoring data."""

import json
from pathlib import Path
from typing import Any, Dict, List, override

from ._gc_monitor import GCMonitorStatsItem
from .exporter import GCMonitorExporter


class TraceExporter(GCMonitorExporter):
    """
    Exporter for Chrome Trace Event format.

    Collects GC monitoring events and exports them as JSON
    compatible with Chrome DevTools Performance panel.
    """

    def __init__(
        self,
        pid: int,
        output_path: Path,
        thread_name: str = "GC Monitor",
        flush_threshold: int = 1000,
    ) -> None:
        """
        Initialize the trace exporter.

        Args:
            pid: Process ID being monitored
            output_path: Path to output file for automatic flushing
            thread_name: Name for the GC monitor thread in trace
            flush_threshold: Number of events to buffer before flushing to file (default: 1000)
        """
        super().__init__(pid, thread_name)
        self._events: List[Dict[str, Any]] = []
        self._flushed_events: List[Dict[str, Any]] = []
        self._flush_threshold = flush_threshold
        self._output_path = output_path
        self._closed = False
        self._metadata_written = False

    @override
    def add_event(self, stats_item: GCMonitorStatsItem) -> None:
        """
        Add a GC monitoring event from GCMonitorStatsItem.

        Args:
            stats_item: GCMonitorStatsItem instance from callback
        """
        # Convert timestamp from nanoseconds to microseconds
        ts_us = int(stats_item.ts / 1_000)
        # Convert duration from seconds to microseconds
        dur_us = int(stats_item.duration * 1_000_000)

        pause_data = {
            "generation": stats_item.gen,
            "collections": stats_item.collections,
            "total_duration": stats_item.total_duration,
            "heap_size": stats_item.heap_size,
            "collected": stats_item.collected,
            "uncollectable": stats_item.uncollectable,
            "candidates": stats_item.candidates,
            "object_visits": stats_item.object_visits,
            "objects_transitively_reachable": stats_item.objects_transitively_reachable,
            "objects_not_transitively_reachable": stats_item.objects_not_transitively_reachable,
            "work_to_do": stats_item.work_to_do,
        }

        counter_data = {
            "heap_size": stats_item.heap_size,
            "collected": stats_item.collected,
            "uncollectable": stats_item.uncollectable,
            "candidates": stats_item.candidates,
            "object_visits": stats_item.object_visits,
        }

        if stats_item.gen == 1:
            counter_data.update(
                {
                    "objects_transitively_reachable": stats_item.objects_transitively_reachable,
                    "objects_not_transitively_reachable": stats_item.objects_not_transitively_reachable,
                    "work_to_do": stats_item.work_to_do,
                }
            )

        # Complete event for GC pause visualization
        self._events.append(
            {
                "name": "GC Pause",
                "cat": "gc.pause",
                "ph": "X",
                "ts": ts_us,
                "dur": dur_us,
                "pid": self._pid,
                "tid": self._thread_name,
                "args": pause_data,
            }
        )

        self._events.append(
            {
                "name": f"GC Pause (gen={stats_item.gen})",
                "cat": f"gc.pause(gen={stats_item.gen})",
                "ph": "X",
                "ts": ts_us,
                "dur": dur_us,
                "pid": self._pid,
                "tid": self._thread_name,
                "args": pause_data,
            }
        )

        # Counter event for memory metrics
        self._events.append(
            {
                "name": f"Memory Counters (gen={stats_item.gen})",
                "cat": f"gc.memory(gen={stats_item.gen})",
                "ph": "C",
                "ts": ts_us,
                "pid": self._pid,
                "tid": self._thread_name,
                "args": counter_data,
            }
        )

        self._events.append(
            {
                "name": "Heap Size",
                "cat": "gc.heap_size",
                "ph": "C",
                "ts": ts_us,
                "pid": self._pid,
                "tid": self._thread_name,
                "args": {
                    "heap_size": stats_item.heap_size,
                },
            }
        )

        # Auto-flush if threshold reached
        if len(self._events) >= self._flush_threshold:
            self._flush()

    def _flush(self) -> None:
        """Flush buffered events to file."""
        if not self._events:
            return

        # Move events to flushed list
        self._flushed_events.extend(self._events)
        self._events.clear()

        # Write all events to file
        self._write_to_file()

    def _write_to_file(self) -> None:
        """Write all flushed and buffered events to file."""
        # Combine flushed events with current buffer
        all_events = list(self._flushed_events)
        all_events.extend(self._events)

        # Add metadata only on first write
        if not self._metadata_written:
            metadata = [
                {
                    "name": "process_name",
                    "ph": "M",
                    "pid": self._pid,
                    "tid": self._thread_name,
                    "args": {"name": f"Python Process (PID: {self._pid})"},
                },
                {
                    "name": "thread_name",
                    "ph": "M",
                    "pid": self._pid,
                    "tid": self._thread_name,
                    "args": {"name": "GC Monitor"},
                },
            ]
            all_events = metadata + all_events
            self._metadata_written = True

        with open(self._output_path, "w", encoding="utf-8") as f:
            json.dump(all_events, f, indent=2)

    @override
    def close(self) -> None:
        """
        Close the exporter and write all events to file.

        Safe to call multiple times - only the first call writes the file.
        """
        if self._closed:
            return
        self._closed = True
        self._write_to_file()

    def clear(self) -> None:
        """Clear all collected events."""
        self._events.clear()
        self._flushed_events.clear()

    def get_event_count(self) -> int:
        """Return the number of collected events."""
        return len(self._events) + len(self._flushed_events)

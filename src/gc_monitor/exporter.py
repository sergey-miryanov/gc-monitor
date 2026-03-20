"""Base exporter interface for GC monitoring data."""

from pathlib import Path

from ._gc_monitor import GCMonitorStatsItem


class GCMonitorExporter:
    """Base class for exporters that collect GC events and save them."""

    def __init__(self, pid: int, thread_name: str = "GC Monitor") -> None:
        self._pid = pid
        self._thread_name = thread_name

    def add_event(self, stats_item: GCMonitorStatsItem) -> None:
        """Add a GC monitoring event."""
        raise NotImplementedError

    def save_json(self, output_path: Path) -> None:
        """Save collected events to JSON file."""
        raise NotImplementedError

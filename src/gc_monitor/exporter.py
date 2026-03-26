"""Base exporter interface for GC monitoring data."""

from .protocol import StatsItem

__all__ = ["GCMonitorExporter"]


class GCMonitorExporter:
    """Base class for exporters that collect GC events and save them."""

    def __init__(
        self,
        pid: int,
        thread_name: str = "GC Monitor",
        thread_id: int | None = None,
    ) -> None:
        self._pid = pid
        self._thread_name = thread_name
        self._thread_id = thread_id

    @property
    def pid(self) -> int:
        """Return the process ID being monitored."""
        return self._pid

    def add_event(self, stats_item: StatsItem) -> None:
        """Add a GC monitoring event."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the exporter and write all events to file."""
        raise NotImplementedError

    def get_event_count(self) -> int:
        """Return the number of events collected."""
        return 0

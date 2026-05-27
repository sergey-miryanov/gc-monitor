"""Base exporter interface for GC monitoring data."""

from ..protocol import TGCStatsInfo, TIncrementalGCStatsInfo, TInstantMsg
from ..target_process import TargetProcessMetadata

__all__ = ["EventsExporter"]


class EventsExporter:
    """Base class for exporters that collect GC events and save them."""

    def __init__(self, metadata: TargetProcessMetadata) -> None:
        self._metadata = metadata

    def add_event(self, pid: int, item: TGCStatsInfo | TIncrementalGCStatsInfo) -> None:
        """Add a GC monitoring event."""
        raise NotImplementedError

    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        """Add a GC monitoring event."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the exporter and write all events to file."""
        raise NotImplementedError

    def get_event_count(self) -> int:
        """
        Return the number of events collected.

        Can be used with closed exporter.
        """
        return 0

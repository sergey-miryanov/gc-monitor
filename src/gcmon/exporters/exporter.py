"""Base exporter interface for GC monitoring data."""

from abc import ABC, abstractmethod

from ..protocol import TGCStatsInfo, TInstantMsg

__all__ = ["EventsExporter"]


class EventsExporter(ABC):
    """Base class for exporters that collect GC events and save them."""

    @abstractmethod
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        """Add a GC monitoring event."""

    @abstractmethod
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        """Add a GC monitoring event."""

    @abstractmethod
    def close(self) -> None:
        """Close the exporter and write all events to file."""

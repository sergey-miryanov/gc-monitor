"""Base exporter interface for GC monitoring data."""

from abc import ABC, abstractmethod

from ..poll_status import ProcessLifecycle
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
    def mark_process_lifecycle(
        self, pid: int, kind: ProcessLifecycle, ts_ns: int,
    ) -> None:
        """Record a process lifecycle transition for *pid*.

        ``ProcessLifecycle.STARTED`` is reported the first time a successful
        poll is observed for the pid; ``ProcessLifecycle.DIED`` is reported
        when the pid is detected as dead (or on graceful monitor stop).
        ``ts_ns`` is a monotonic timestamp captured at the moment of the
        transition in the monitor's own clock domain.

        Exporters that do not model a per-process lifetime track (e.g. JSONL
        / Chrome JSON) may treat this as a no-op.
        """

    @abstractmethod
    def close(self) -> None:
        """Close the exporter and write all events to file."""



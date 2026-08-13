"""Base exporter interface for GC monitoring data."""

from abc import ABC, abstractmethod
from collections.abc import Set

from ..protocol import TGCStatsInfo, TInstantMsg, TLossMsg

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

    def add_rss_sample(self, pid: int, rss_bytes: int, ts_ns: int) -> None:  # noqa: B027
        """Record an RSS sample for *pid*. No-op in the base class."""

    def add_loss_event(self, pid: int, item: TLossMsg) -> None:  # noqa: B027
        """Record a poll interval whose GC records never reached gcmon.

        One call per interpreter, whatever went blind in it. No-op in the
        base class.
        """

    def add_process_liveness(self, pids: Set[int], ts_ns: int) -> None:  # noqa: B027
        """Record that gcmon read GC state out of every pid in *pids* at
        *ts_ns*. No-op in the base class.

        One call per monitor tick carries the whole live set. Only the
        Perfetto path acts on it, widening each pid's ``Processes``-track
        span; see ADR-0011.
        """

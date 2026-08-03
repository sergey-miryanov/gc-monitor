"""Core GC monitoring functionality."""

import logging
import time
from _remote_debugging import get_child_pids, get_gc_stats
from collections.abc import Sequence, Set
from typing import Self

from .exporters import EventsExporter
from .poll_status import PollStatus
from .protocol import TGCStatsInfo
from .stats import StreamingStats
from .target_process import TargetProcess

logger = logging.getLogger("gcmon")

__all__ = ["EventsMonitor", "create_monitor"]

# (iid, gen): one ring buffer per interpreter and generation, each with its
# own `collections` counter.
type CursorKey = tuple[int, int]


def _is_complete(event: TGCStatsInfo) -> bool:
    """False for a slot holding no finished collection: never written, or
    mid-write with ``ts_start`` published and ``ts_stop`` not yet."""
    return event.ts_start < event.ts_stop


class EventsMonitor:
    def __init__(
        self,
        process: TargetProcess,
        exporter: EventsExporter,
        stats: StreamingStats,
    ) -> None:

        self._process = process
        self._exporter = exporter
        self._enabled = True
        self._cursors: dict[int, dict[CursorKey, int]] = {}
        self._stats = stats

    def get_child_pids(self) -> list[int] | None:
        """Every descendant of the target, or ``None`` when the tree could
        not be read. ``None`` is not an empty tree: a caller that prunes
        state for missing pids has to skip that tick.
        """
        try:
            return get_child_pids(self._process.pid, recursive=True)
        except Exception as exc:
            logger.warning(
                "Monitor for PID %s encountered error while gathering children PIDs", self._process.pid, exc_info=exc
            )
            return None

    def poll(self, pid: int) -> PollStatus:

        if not self._enabled:
            logger.warning(
                "Monitor for PID %s already stopped",
                pid,
            )
            return PollStatus.FAIL

        try:
            ts_read_start = time.monotonic_ns()
            events = get_gc_stats(pid, all_interpreters=True)
            ts_read_stop = time.monotonic_ns()
            self._stats.record_read_time(ts_read_stop - ts_read_start)
            self._ingest(pid, events)

            return PollStatus.OK
        except RuntimeError as exc:
            logger.debug("Error while polling PID %s (child PID=%s): %s", self._process.pid, pid, exc)
            return PollStatus.INVALID_PROCESS
        except PermissionError as exc:
            logger.debug("Error while polling PID %s (child PID=%s): %s", self._process.pid, pid, exc)
            return PollStatus.INVALID_PROCESS
        except Exception as exc:
            logger.warning("Monitor for PID %s (child PID=%s) encountered error", self._process.pid, pid, exc_info=exc)
            return PollStatus.FAIL

    def forget(self, pid: int) -> None:
        """Drop every cursor held for *pid*, so a reused pid inherits no
        counter."""
        self._cursors.pop(pid, None)

    def retain(self, pids: Set[int]) -> None:
        """Drop the cursors of every pid outside *pids*.

        A process that exits between two ticks is never polled again, so no
        wait policy gives up on it and ``forget`` never runs.
        """
        for pid in self._cursors.keys() - pids:
            del self._cursors[pid]

    def _ingest(self, pid: int, events: Sequence[TGCStatsInfo]) -> None:
        """Emit the records in *events* not seen yet.

        Every poll returns the whole ring buffer, so ``collections`` is what
        identifies a record.
        """
        cursors = self._cursors.setdefault(pid, {})

        fresh: dict[tuple[int, int, int], TGCStatsInfo] = {}
        for event in events:
            if not _is_complete(event):
                continue
            if event.collections <= cursors.get((event.iid, event.gen), 0):
                continue
            # Two slots with the same counter are one collection: the target
            # copies a record forward before overwriting it.
            fresh.setdefault((event.iid, event.gen, event.collections), event)

        # Slot order is not time order: the batch arrives rotated around the
        # ring's write position, with the generations concatenated.
        for event in sorted(fresh.values(), key=lambda event: event.ts_start):
            self._exporter.add_event(pid, event)
            self._stats.update(pid, event)
            key = (event.iid, event.gen)
            cursors[key] = max(cursors.get(key, 0), event.collections)

    def stop(self) -> None:
        """Stop monitoring and close the handler and exporter.

        Safe to call multiple times.
        """
        self._exporter.close()
        self._enabled = False

    @property
    def is_enabled(self) -> bool:
        """Check if monitor is currently enabled."""
        return self._enabled

    @property
    def pid(self) -> int:
        """Return the process ID being monitored."""
        return self._process.pid

    @property
    def exporter(self) -> EventsExporter:
        return self._exporter

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()


def create_monitor(
    process: TargetProcess,
    exporter: EventsExporter,
    stats: StreamingStats,
) -> EventsMonitor:
    """Create a GCMonitor for the given process.

    Args:
        process: Target process to monitor.
        exporter: Events exporter.

    Returns:
        A GCMonitor instance ready to be polled.
    """
    return EventsMonitor(process, exporter, stats)

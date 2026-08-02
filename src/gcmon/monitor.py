"""Core GC monitoring functionality."""

import logging
import time
from _remote_debugging import get_child_pids, get_gc_stats
from collections.abc import Sequence
from typing import Self

from .exporters import EventsExporter
from .poll_status import PollStatus
from .protocol import TGCStatsInfo
from .stats import StreamingStats
from .target_process import TargetProcess

logger = logging.getLogger("gcmon")

__all__ = ["EventsMonitor", "create_monitor"]

# One ring buffer: CPython keeps a separate one, with its own `collections`
# counter, per interpreter and per generation.
type CursorKey = tuple[int, int]


def _is_complete(event: TGCStatsInfo) -> bool:
    """Skip slots holding no finished collection: never written (all zeros),
    or mid-write, where CPython has published ``ts_start`` but not yet
    ``ts_stop``. The following poll reads those once they are whole."""
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

    def get_child_pids(self) -> list[int]:
        try:
            return get_child_pids(self._process.pid, recursive=True)
        except Exception as exc:
            logger.warning(
                "Monitor for PID %s encountered error while gathering children PIDs", self._process.pid, exc_info=exc
            )
            return []

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
        """Drop every cursor held for *pid*. The poll loop calls this after
        the process stops answering, so a reused pid inherits no counter."""
        self._cursors.pop(pid, None)

    def _ingest(self, pid: int, events: Sequence[TGCStatsInfo]) -> None:
        """Emit the records in *events* not seen yet.

        Every poll returns the whole ring buffer, so ``collections``, the
        target's per-generation counter, identifies the new records.
        """
        cursors = self._cursors.setdefault(pid, {})
        self._rebaseline_restarted(pid, cursors, events)

        fresh: dict[tuple[int, int, int], TGCStatsInfo] = {}
        for event in events:
            if not _is_complete(event):
                continue
            if event.collections <= cursors.get((event.iid, event.gen), 0):
                continue
            # Two slots reporting the same counter are one collection: the
            # target copies a record forward before overwriting it.
            fresh.setdefault((event.iid, event.gen, event.collections), event)

        # Slot order is not time order. The batch arrives rotated around the
        # ring's write position, with the generations concatenated, so sort
        # before emitting.
        for event in sorted(fresh.values(), key=lambda event: event.ts_start):
            self._exporter.add_event(pid, event)
            self._stats.update(pid, event)
            key = (event.iid, event.gen)
            cursors[key] = max(cursors.get(key, 0), event.collections)

    def _rebaseline_restarted(self, pid: int, cursors: dict[CursorKey, int], events: Sequence[TGCStatsInfo]) -> None:
        """Forget cursors whose counter has gone backwards.

        ``collections`` only rises, so a lower value means a different
        process or interpreter. A stale cursor would otherwise reject
        everything until the new counter overtook it.
        """
        highest: dict[CursorKey, int] = {}
        for event in events:
            if not _is_complete(event):
                continue
            key = (event.iid, event.gen)
            highest[key] = max(highest.get(key, 0), event.collections)

        for key, high in highest.items():
            cursor = cursors.get(key)
            if cursor is not None and high < cursor:
                logger.debug(
                    "PID %s (iid=%s, gen=%s) collection counter went backwards (%s -> %s); "
                    "treating it as a new process or interpreter",
                    pid,
                    key[0],
                    key[1],
                    cursor,
                    high,
                )
                del cursors[key]

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

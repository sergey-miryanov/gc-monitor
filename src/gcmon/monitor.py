"""Core GC monitoring functionality."""

import logging
import time
from _remote_debugging import get_child_pids, get_gc_stats
from collections.abc import Sequence, Set
from itertools import groupby
from typing import Self

from .exporters import EventsExporter
from .loss import (
    CursorKey,
    KeyAccumulator,
    LossWindow,
    confirmed_by_interpreter,
    merge_windows,
    split_around,
    to_loss_msg,
)
from .poll_status import PollStatus
from .protocol import TGCStatsInfo
from .stats import StreamingStats
from .target_process import TargetProcess

logger = logging.getLogger("gcmon")

__all__ = ["EventsMonitor", "create_monitor"]


def _is_complete(event: TGCStatsInfo) -> bool:
    """False for a slot holding no finished collection: never written, or
    mid-write with ``ts_start`` published and ``ts_stop`` not yet."""
    return event.ts_start < event.ts_stop


def _in_flight(events: Sequence[TGCStatsInfo]) -> dict[int, int]:
    """Per interpreter, the ``ts_start`` of the collection running at the read.

    A slot with ``ts_start`` published and no ``ts_stop`` yet is one the GC is
    inside. Collections in an interpreter are serialized, so there is at most
    one, and taking the newest ``ts_start`` picks it out from slots that were
    never written at all.
    """
    started: dict[int, int] = {}
    for event in events:
        if not _is_complete(event):
            started[event.iid] = max(started.get(event.iid, 0), event.ts_start)

    return {iid: ts for iid, ts in started.items() if ts > 0}


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
        self._cursors: dict[int, dict[CursorKey, KeyAccumulator]] = {}
        self._in_flight_starts: dict[int, dict[int, int]] = {}
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
        self._in_flight_starts.pop(pid, None)

    def retain(self, pids: Set[int]) -> None:
        """Drop the cursors of every pid outside *pids*.

        A process that exits between two ticks is never polled again, so no
        wait policy gives up on it and ``forget`` never runs.
        """
        for pid in self._cursors.keys() - pids:
            del self._cursors[pid]
        for pid in self._in_flight_starts.keys() - pids:
            del self._in_flight_starts[pid]

    def _ingest(self, pid: int, events: Sequence[TGCStatsInfo]) -> None:
        """Emit the records in *events* not seen yet.

        Every poll returns the whole ring buffer, so ``collections`` is what
        identifies a record.
        """
        cursors = self._cursors.setdefault(pid, {})
        confirmed = confirmed_by_interpreter(cursors)

        # A collection the previous read caught mid-flight confirms that
        # interpreter, whatever generation it belongs to. The GC was inside it
        # then and nothing newer had finished: a record lost since carries a
        # higher counter than anything the read saw, and had it completed
        # before the read it would have been what the read saw. Collections
        # are serialized, so everything lost since ran after it.
        #
        # Its `ts_start` is the bound, and the strongest one available, since a
        # collection that had started is later evidence than the newest one
        # that had finished. It also survives the record never coming back: the
        # slot can be overwritten before the next read and the interval stays
        # bounded. Learning where it ended raises the bound further, so both
        # apply.
        for iid, since in self._in_flight_starts.get(pid, {}).items():
            confirmed[iid] = max(confirmed.get(iid, 0), since)
            finished = [e.ts_stop for e in events if e.iid == iid and _is_complete(e) and e.ts_start <= since]
            if finished:
                confirmed[iid] = max(confirmed[iid], max(finished))
        self._in_flight_starts[pid] = _in_flight(events)

        # Slot order is not time order: the batch arrives rotated around the
        # ring's write position, with the generations concatenated. Sorting
        # puts every ring back into the counter order its accumulator folds in.
        ordered = sorted(
            (event for event in events if _is_complete(event)),
            key=lambda event: (event.iid, event.gen, event.collections),
        )

        fresh: list[TGCStatsInfo] = []
        windows: dict[int, list[LossWindow]] = {}
        observed: dict[int, list[tuple[int, int]]] = {}
        for (iid, gen), group in groupby(ordered, key=lambda event: (event.iid, event.gen)):
            accumulator = cursors.setdefault((iid, gen), KeyAccumulator())
            seen = accumulator.last
            # Keying on the counter drops the copy the target makes of a record
            # ahead of overwriting it: both slots report the same counter, so no
            # threshold tells them apart.
            run = list({event.collections: event for event in group if event.collections > seen}.values())

            window = accumulator.observe_batch(run, confirmed.get(iid, 0))
            if window is not None:
                windows.setdefault(iid, []).append(window)
                self._stats.record_loss(pid, gen, window.lost_count, window.lost_pause_ns)
            if run:
                self._stats.record_lifetime(pid, iid, gen, accumulator.last, accumulator.last_duration)
                observed.setdefault(iid, []).extend((event.ts_start, event.ts_stop) for event in run)
            fresh.extend(run)

        # A window runs to the next record on its own key, the last thing the
        # two polls prove about that key. Collections observed inside it earn
        # a hole rather than a bound: no lost record ran during one that was
        # seen, so what is left after the holes is where the missing records
        # must be. One gen-0 window bracketing an observed gen-1 collection
        # therefore draws as two pieces, one either side of it.
        for iid, opened in windows.items():
            # Every window that can overlap another opened in this same poll,
            # so merging here is enough to keep the loss track laminar.
            for merged in merge_windows(opened):
                for piece in split_around(merged, observed.get(iid, ())):
                    self._exporter.add_loss_event(pid, to_loss_msg(iid, piece))

        # One interpreter's generations share a track, so they go out in time
        # order. Two interpreters share no track and collect concurrently, so
        # nothing is claimed about the order between them.
        for event in sorted(fresh, key=lambda event: (event.iid, event.ts_start)):
            self._exporter.add_event(pid, event)
            self._stats.update(pid, event)

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

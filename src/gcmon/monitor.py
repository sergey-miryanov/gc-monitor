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
    KeyGap,
    to_loss_msg,
)
from .poll_status import PollStatus
from .protocol import TGCStatsInfo
from .stats import StreamingStats
from .target_process import TargetProcess

logger = logging.getLogger("gcmon")

__all__ = ["EventsMonitor", "create_monitor"]


def _is_complete(event: TGCStatsInfo) -> bool:
    """False for a slot holding no finished record: never written, or
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
        self._cursors: dict[int, dict[CursorKey, KeyAccumulator]] = {}
        # When this pid was last read. A loss record is bounded by two polls,
        # so a pid gcmon has polled once has nothing to bound yet.
        self._polled_at: dict[int, int] = {}
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
            self._stats.record_ring_geometry(events)
            self._ingest(pid, events, ts_read_start)

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
        self._polled_at.pop(pid, None)

    def retain(self, pids: Set[int]) -> None:
        """Drop the cursors of every pid outside *pids*.

        A process that exits between two ticks is never polled again, so no
        wait policy gives up on it and ``forget`` never runs.
        """
        for pid in self._cursors.keys() - pids:
            del self._cursors[pid]
        for pid in self._polled_at.keys() - pids:
            del self._polled_at[pid]

    def _ingest(self, pid: int, events: Sequence[TGCStatsInfo], ts_poll: int) -> None:
        """Emit the records in *events* not seen yet.

        Every poll returns the whole ring buffer, so ``collections`` is what
        identifies a record.

        *ts_poll* is when this read began, on the monitor's own clock, which
        the target's timestamps and the RSS samples already share. It bounds
        any loss this poll finds together with the previous poll's, and the
        pair is stored under the same field for both, so consecutive intervals
        tile the timeline instead of overlapping by a read.
        """
        cursors = self._cursors.setdefault(pid, {})
        polled_before = self._polled_at.get(pid)
        self._polled_at[pid] = ts_poll

        # Slot order is not time order: the batch arrives rotated around the
        # ring's write position, with the generations concatenated. Sorting
        # puts every ring back into the counter order its accumulator folds in.
        ordered = sorted(
            (event for event in events if _is_complete(event)),
            key=lambda event: (event.iid, event.gen, event.collections),
        )

        fresh: list[TGCStatsInfo] = []
        observed: dict[int, dict[int, int]] = {}
        gaps: dict[int, dict[int, KeyGap]] = {}
        for (iid, gen), group in groupby(ordered, key=lambda event: (event.iid, event.gen)):
            accumulator = cursors.setdefault((iid, gen), KeyAccumulator())
            seen = accumulator.last_collections
            # Keying on the counter drops the copy the target makes of a record
            # ahead of overwriting it: both slots report the same counter, so no
            # threshold tells them apart.
            #
            # A dict keeps the last of a duplicate pair, which the sort leaves
            # in slot order. Which one survives cannot matter: `add_stats`
            # memcpy's the record forward before touching any field, so until
            # it stores the new `ts_start` the twin is byte-identical, and from
            # that store until it increments `collections` the slot carries a
            # start later than its stale stop, which `_is_complete` rejects.
            # A duplicate that reaches here is therefore a copy of its twin.
            # The choice would matter if that ever stopped holding: this run's
            # last record sets `last_ts_stop` and `last_duration`, which are
            # the pause base the next poll subtracts from.
            run = list({event.collections: event for event in group if event.collections > seen}.values())

            gap = accumulator.observe_batch(run)
            if run:
                observed.setdefault(iid, {})[gen] = len(run)
                self._stats.record_lifetime(pid, iid, gen, accumulator.last_collections, accumulator.last_duration)
            if gap is not None:
                # Record first, draw second, and never the other way round.
                # The counts are the target's own counters, so `Cov`, `F` and
                # the exact totals hold whether or not a span comes of this gap.
                self._stats.record_loss(pid, gen, gap.lost_count, gap.lost_pause_ns)
                gaps.setdefault(iid, {})[gen] = gap
            fresh.extend(run)

        # One record per poll interval, per interpreter, carrying every
        # generation that went blind in it and every one that collected. See
        # ADR-0015 for why the interval is the unit.
        for iid, lost in gaps.items():
            if polled_before is None:
                # One poll bounds nothing. Nothing can reach here anyway, since
                # a key seeds on the first records it returns and seeding opens
                # no gap.
                continue
            self._exporter.add_loss_event(
                pid,
                to_loss_msg(iid, polled_before, ts_poll, observed.get(iid, {}), lost),
            )

        # One interpreter's generations share a track, so they go out in time
        # order. Two interpreters share no track and collect concurrently, so
        # nothing is claimed about the order between them.
        for event in sorted(fresh, key=lambda event: (event.iid, event.ts_start)):
            self._exporter.add_event(pid, event)
            self._stats.update(pid, event)

        # Both halves of this poll are folded now: the gaps above, the records
        # just here. Coverage divides one into the other, so asking any earlier
        # measures a gap against a sample missing the very records that came
        # back with it.
        self._stats.check_coverage_advisory(pid)

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

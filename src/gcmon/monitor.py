"""Polling a process for GC records and passing them to the stats and the
exporters."""

import logging
import time
from _remote_debugging import get_child_pids, get_gc_stats
from collections.abc import Sequence, Set
from itertools import groupby
from typing import Self

import msgspec

from .data import GenLoss, LossMsg
from .exporters import EventsExporter
from .loss import (
    RingAccumulator,
    RingKey,
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


class PidState(msgspec.Struct):
    """What gcmon carries from one poll of a process to the next."""

    rings: dict[RingKey, RingAccumulator] = msgspec.field(default_factory=dict)
    # When gcmon last read this pid, and None before the first read. Two polls
    # bound a loss record, so one poll bounds nothing.
    polled_at: int | None = None


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
        self._pids: dict[int, PidState] = {}
        self._stats = stats

    def get_child_pids(self) -> list[int] | None:
        """Every descendant of the target, or ``None`` when the read failed.

        An empty list means no children. ``None`` means no answer, so a caller
        pruning state for missing pids skips that tick.
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
        """Drop everything held for *pid*, so a reused pid inherits no counter
        and no poll instant from the process before it."""
        self._pids.pop(pid, None)

    def retain(self, pids: Set[int]) -> None:
        """Drop the state of every pid outside *pids*.

        A process that exits between two ticks is never polled again, so no
        wait policy gives up on it and ``forget`` never runs.
        """
        for pid in self._pids.keys() - pids:
            del self._pids[pid]

    def _ingest(self, pid: int, events: Sequence[TGCStatsInfo], ts_poll: int) -> None:
        """Emit the records in *events* not seen yet.

        Every poll returns the whole ring buffer, so ``collections`` is what
        identifies a record.

        *ts_poll* is when this read began, on the monitor's own clock, the one
        the target's timestamps and the RSS samples already share. It closes
        the interval the previous poll opened. Both edges come from the same
        point of a read, so consecutive intervals tile the timeline rather
        than overlapping by a read's width.
        """
        state = self._pids.setdefault(pid, PidState())
        polled_before = state.polled_at
        state.polled_at = ts_poll

        # The batch arrives rotated around each ring's write position, with the
        # generations concatenated. Sorting puts every ring back into the
        # counter order its accumulator folds in.
        ordered = sorted(
            (event for event in events if _is_complete(event)),
            key=lambda event: (event.iid, event.gen, event.collections),
        )

        fresh: list[TGCStatsInfo] = []
        entries: dict[int, list[GenLoss]] = {}
        for (iid, gen), group in groupby(ordered, key=lambda event: (event.iid, event.gen)):
            accumulator = state.rings.setdefault((iid, gen), RingAccumulator())
            # The ring decides what it has not handed over yet, cursor and
            # duplicate slots both. ADR-0015 rests that filter and
            # `_is_complete` above on CPython's publishing contract.
            streak = accumulator.unseen(group)
            if not streak:
                # Nothing new on this ring, so it contributed neither loss nor
                # coverage and the interval leaves it out.
                continue

            entry = accumulator.observe_batch(streak)
            entries.setdefault(iid, []).append(entry)
            self._stats.record_lifetime(pid, iid, gen, accumulator.last_collections, accumulator.last_duration)
            if entry.lost_count:
                # Record first, draw second. The counts are the target's own
                # counters, so `Cov`, `F` and the exact totals hold whether or
                # not a span is drawn.
                self._stats.record_loss(pid, gen, entry.lost_count, entry.lost_pause_ns)
            fresh.extend(streak)

        # One record per poll interval, per interpreter, carrying every
        # generation that went blind in it and every one that collected. See
        # ADR-0015 for why the interval is the unit. `ordered` sorts on
        # `(iid, gen, ...)`, so each interpreter's entries already sit in the
        # order a reader scans them on the slice.
        for iid, gens in entries.items():
            # An interval that lost nothing draws no span.
            if not any(entry.lost_count for entry in gens):
                continue
            # Reaching here means gcmon polled this pid before. A first poll
            # builds every ring it touches, and a ring seeds on the first
            # records it returns without opening a gap, so no entry above could
            # carry loss.
            assert polled_before is not None
            self._exporter.add_loss_event(pid, LossMsg(iid=iid, ts_start=polled_before, ts_stop=ts_poll, gens=gens))

        # The sort serves the JSONL capture, where `add_event` appends a line
        # per call and nothing reorders GC records on read-back, so these lines
        # carry the only time order per interpreter that file has. A trace
        # needs none of it: GC runs inside an interpreter are serialized, so a
        # processor sorting by timestamp rebuilds the track from any order, and
        # `_loss_in_time_order` covers the track where order is load-bearing.
        for event in sorted(fresh, key=lambda event: (event.iid, event.ts_start)):
            self._exporter.add_event(pid, event)
            self._stats.update(pid, event)

        # This poll has folded both halves now: the gaps above, the records
        # just here. Coverage divides one into the other, so checking earlier
        # would measure a gap against a sample missing the records that came
        # back with it.
        self._stats.check_coverage_advisory(pid)

    def stop(self) -> None:
        """Close the exporter and stop accepting polls.

        Safe to call more than once.
        """
        self._exporter.close()
        self._enabled = False

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def pid(self) -> int:
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
    """An :class:`EventsMonitor` for *process*, ready to be polled."""
    return EventsMonitor(process, exporter, stats)

"""Reconstructing the GC records a poll could not observe.

A target collecting faster than gcmon polls overwrites records before they
are read. Two cumulative fields make the loss measurable: ``collections``
counts what was missed, and ``duration`` gives the pause time nobody saw.

``EventsMonitor`` owns one ``KeyAccumulator`` per ``(pid, iid, gen)``; see
ADR-0015 for why the merged spans need a track of their own.
"""

from collections.abc import Iterable, Mapping, Sequence

import msgspec

from .data import LossMsg, secs_to_ns
from .protocol import TGCStatsInfo

__all__ = [
    "CursorKey",
    "KeyAccumulator",
    "LossWindow",
    "MergedLoss",
    "merge_by_interpreter",
    "merge_windows",
    "to_loss_msg",
]

# (iid, gen). One CPython ring buffer, with its own `collections` counter.
type CursorKey = tuple[int, int]


class LossWindow(msgspec.Struct):
    """An interval on one key in which records were overwritten unread.

    Bounded by the ``ts_stop`` of the last observed record before the gap and
    the ``ts_start`` of the first one after it.
    """

    ts_start: int
    ts_stop: int
    gen: int
    lost_count: int
    lost_pause_ns: int


class MergedLoss(msgspec.Struct):
    """Overlapping windows of one interpreter collapsed into one span.

    ``lost_count`` and ``lost_pause_ns`` are keyed by generation.
    """

    ts_start: int
    ts_stop: int
    lost_count: dict[int, int] = msgspec.field(default_factory=dict)
    lost_pause_ns: dict[int, int] = msgspec.field(default_factory=dict)


class KeyAccumulator(msgspec.Struct):
    """What one ring buffer did, against what gcmon saw of it.

    ``last`` doubles as the poll cursor: a record whose ``collections`` does
    not exceed it was already emitted, or was overwritten and is gone.
    """

    first: int = 0
    first_pause_ns: int = 0
    first_duration: float = 0.0
    last: int = 0
    last_duration: float = 0.0
    last_ts_stop: int = 0
    sampled_count: int = 0
    sampled_pause_ns: int = 0
    windows: list[LossWindow] = msgspec.field(default_factory=list)

    def observe_batch(self, events: Sequence[TGCStatsInfo]) -> None:
        """Fold one poll's run of records for this key, in counter order.

        A ring holds consecutive records, so only the run's first record can
        sit across a gap and only its last one settles the cursor.

        The run must be sorted by counter, past ``last``, and free of the
        copy the target makes of a record ahead of overwriting it; ``_ingest``
        guarantees all three. Contiguity is trusted rather than checked, see
        ADR-0015.
        """
        if not events:
            return

        self._open_run(events[0])

        for event in events:
            self.sampled_pause_ns += event.ts_stop - event.ts_start

        last = events[-1]
        self.sampled_count += len(events)
        self.last = last.collections
        self.last_duration = last.duration
        self.last_ts_stop = last.ts_stop

    def _open_run(self, first: TGCStatsInfo) -> None:
        """Seed the span, or record the gap the run sits behind.

        Touches no running total; :meth:`observe_batch` owns those.
        """
        if self.sampled_count == 0:
            self.first = first.collections
            self.first_pause_ns = first.ts_stop - first.ts_start
            self.first_duration = first.duration
            return

        lost = first.collections - self.last - 1
        if lost <= 0:
            return

        # Delta duration spans the records after `last` through this one, so
        # taking this one's own pause back out leaves the `lost` records alone.
        spanned_ns = secs_to_ns(first.duration - self.last_duration)
        self.windows.append(
            LossWindow(
                ts_start=self.last_ts_stop,
                ts_stop=first.ts_start,
                gen=first.gen,
                lost_count=lost,
                lost_pause_ns=spanned_ns - (first.ts_stop - first.ts_start),
            )
        )

    @property
    def exact_count(self) -> int:
        """Collections over the observed span, counting both ends.

        What the target collected before the first observed record is outside
        the span: gcmon cannot tell "ran before we attached" from "lost".
        """
        if self.sampled_count == 0:
            return 0
        return self.last - self.first + 1

    @property
    def exact_pause_ns(self) -> int:
        """Pause time over the same span, from the target's own accumulator.

        ``first_duration`` is cumulative through the first observed record, so
        the delta starts after it. Adding that record's pause back is what
        makes this cover the collections ``exact_count`` counts.
        """
        if self.sampled_count == 0:
            return 0
        return secs_to_ns(self.last_duration - self.first_duration) + self.first_pause_ns

    @property
    def lost_count(self) -> int:
        return self.exact_count - self.sampled_count

    @property
    def coverage(self) -> float:
        """Observed share of the span, in ``[0, 1]``.

        An empty accumulator reports 1.0: it lost none of the nothing it
        covers, and every call site would otherwise guard a division.
        """
        if self.exact_count == 0:
            return 1.0
        return self.sampled_count / self.exact_count

    @property
    def scale_factor(self) -> float:
        """Multiplier taking a sampled pause sum to the exact one.

        Sub-phases have no exact counterpart, since CPython accumulates a
        total for the pause alone, but they partition it — so scaling a
        measured phase sum by this estimates it. Percentiles it cannot
        correct; see ADR-0015.
        """
        if self.sampled_pause_ns == 0:
            return 1.0
        return self.exact_pause_ns / self.sampled_pause_ns


def merge_windows(windows: Iterable[LossWindow]) -> list[MergedLoss]:
    """Collapse overlapping windows into disjoint spans, in time order.

    Windows on one key never overlap, being consecutive gaps in one sequence,
    but windows from different generations cross whenever the records bounding
    them interleave. Merging keeps their shared track laminar without clipping
    a span the way ADR-0011's sweep has to.

    Touching windows merge: apart they would draw two slices with nothing
    between them.
    """
    merged: list[MergedLoss] = []

    for window in sorted(windows, key=lambda w: (w.ts_start, w.ts_stop)):
        if merged and window.ts_start <= merged[-1].ts_stop:
            current = merged[-1]
            current.ts_stop = max(current.ts_stop, window.ts_stop)
        else:
            current = MergedLoss(ts_start=window.ts_start, ts_stop=window.ts_stop)
            merged.append(current)

        gen = window.gen
        current.lost_count[gen] = current.lost_count.get(gen, 0) + window.lost_count
        current.lost_pause_ns[gen] = current.lost_pause_ns.get(gen, 0) + window.lost_pause_ns

    return merged


def to_loss_msg(iid: int, merged: MergedLoss) -> LossMsg:
    """Flatten a merged span into the record the exporters carry.

    A generation absent from the span contributes zero, which is also what a
    reader should see: the span exists, that generation lost nothing in it.
    """
    counts = merged.lost_count
    pauses = merged.lost_pause_ns
    return LossMsg(
        iid=iid,
        ts_start=merged.ts_start,
        ts_stop=merged.ts_stop,
        lost_gen_0=counts.get(0, 0),
        lost_gen_1=counts.get(1, 0),
        lost_gen_2=counts.get(2, 0),
        lost_pause_gen_0=pauses.get(0, 0),
        lost_pause_gen_1=pauses.get(1, 0),
        lost_pause_gen_2=pauses.get(2, 0),
    )


def merge_by_interpreter(cursors: Mapping[CursorKey, KeyAccumulator]) -> dict[int, list[MergedLoss]]:
    """Merge one pid's windows, per interpreter.

    Generations merge together because they share a track; interpreters do
    not, because they do not.
    """
    by_iid: dict[int, list[LossWindow]] = {}
    for (iid, _gen), accumulator in cursors.items():
        if accumulator.windows:
            by_iid.setdefault(iid, []).extend(accumulator.windows)

    return {iid: merge_windows(windows) for iid, windows in by_iid.items()}

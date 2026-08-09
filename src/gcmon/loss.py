"""Reconstructing the GC records a poll could not observe.

A target collecting faster than gcmon polls overwrites records before anyone
reads them. Two cumulative fields make the loss measurable: ``collections``
counts what was missed, and ``duration`` gives the pause time nobody saw.

``EventsMonitor`` owns one ``KeyAccumulator`` per ``(pid, iid, gen)``; see
ADR-0015 for why the spans need a track of their own.
"""

from collections.abc import Iterable, Mapping, Sequence

import msgspec

from .data import LossMsg, secs_to_ns
from .protocol import TGCStatsInfo

__all__ = [
    "CursorKey",
    "KeyAccumulator",
    "LossWindow",
    "confirmed_by_interpreter",
    "stack_order",
    "to_loss_msg",
]

# (iid, gen). One CPython ring buffer, with its own `collections` counter.
type CursorKey = tuple[int, int]


class LossWindow(msgspec.Struct):
    """An interval on one key in which records were overwritten unread.

    Bounded by the ``ts_stop`` of the last observed record before the gap and
    the ``ts_start`` of the first one after it, and drawn as itself: one span
    for one generation, at the full width its bounding records describe. Every
    number on it is the target's own counter over those bounds, so none of it
    is a share of anything. The span can therefore cover a collection of
    another generation that gcmon did observe. That collection is drawn on the
    interpreter's own row directly above, which is where a reader narrows the
    span from.

    ``lost_from`` names the missing collections rather than just counting
    them. The gap is found by subtracting two of the ring's own cumulative
    counters, so both bounds are already in hand; keeping the near one costs a
    field and makes the reconstruction checkable, since every collection
    between the first and last gcmon observed on a ring then falls in exactly
    one place — a drawn ``GC Pause`` slice, or one window's
    ``lost_from``..:func:`lost_to <gcmon.data.lost_to>` range.
    """

    ts_start: int
    ts_stop: int
    gen: int
    lost_from: int
    lost_count: int
    lost_pause_ns: int

    @property
    def is_drawable(self) -> bool:
        """Whether the bounds leave the lost records room to have run in.

        A ``ts_stop`` that does not follow ``ts_start`` contradicts the window
        on its face: records were overwritten unread, and records take time.
        Equal bounds say the same thing and would draw as an invisible
        sub-pixel slice. Either way the span belongs on no track: the loss row
        is a Perfetto stack, and :func:`stack_order` sorts windows it takes to
        be well-formed.

        Two things reach here and gcmon cannot separate them. ``ts_stop`` is a
        timestamp this ring published. ``ts_start`` is
        :func:`confirmed_by_interpreter`, a maximum across *all* of the
        interpreter's rings, which is what lets the first one in. A poll
        copies those rings over ~0.6 ms while the target keeps collecting, so
        a collection finishing after its own ring was copied but before a
        later ring's is missed by that poll, and the later ring carries a
        newer ``ts_stop``. The window then opens after the record it bounds
        with nothing misbehaving. ADR-0015 §"What gcmon trusts the target for"
        reaches for the same non-atomicity to explain a torn read. The second
        cause is that section's barrier-free stores exposing a record
        assembled from two collections, which is a target bug. Neither leaves
        a fingerprint the other does not, so nothing here names a culprit.

        Only the drawing is at stake. ``lost_count`` and ``lost_from`` are
        counter arithmetic with no timestamp in them, so ``_ingest`` records
        the loss whatever this says and skips only the span.
        """
        return self.ts_start < self.ts_stop


class KeyAccumulator(msgspec.Struct):
    """What one ring buffer did, against what gcmon saw of it.

    ``last`` doubles as the poll cursor: a record whose ``collections`` does
    not exceed it has gone out already, or the ring overwrote it.
    """

    first: int = 0
    first_pause_ns: int = 0
    first_duration: float = 0.0
    last: int = 0
    last_duration: float = 0.0
    last_ts_stop: int = 0
    sampled_count: int = 0
    sampled_pause_ns: int = 0

    def observe_batch(self, events: Sequence[TGCStatsInfo], confirmed_ts: int = 0) -> LossWindow | None:
        """Fold one poll's run of records for this key, in counter order.

        A ring holds consecutive records, so only the run's first record can
        sit across a gap and only its last one settles the cursor. Returns
        the window that gap opened, if any, for the caller to emit.

        *confirmed_ts* is the latest record seen anywhere in this interpreter
        before this poll. A poll that found the counter unchanged proves
        nothing was lost up to that read, so the window cannot start earlier.

        The run must be sorted by counter, past ``last``, and free of the
        copy the target makes of a record ahead of overwriting it; ``_ingest``
        guarantees all three. Contiguity it trusts without checking, see
        ADR-0015.
        """
        if not events:
            return None

        window = self._open_run(events[0], confirmed_ts)

        for event in events:
            self.sampled_pause_ns += event.ts_stop - event.ts_start

        last = events[-1]
        self.sampled_count += len(events)
        self.last = last.collections
        self.last_duration = last.duration
        self.last_ts_stop = last.ts_stop

        return window

    def _open_run(self, first: TGCStatsInfo, confirmed_ts: int) -> LossWindow | None:
        """Seed the span, or describe the gap the run sits behind.

        Touches no running total; :meth:`observe_batch` owns those.
        """
        if self.sampled_count == 0:
            self.first = first.collections
            self.first_pause_ns = first.ts_stop - first.ts_start
            self.first_duration = first.duration
            return None

        # `last` is the newest counter this key returned, so the ring
        # overwrote `last + 1` onward up to the one before `first`. Both fences
        # are the target's own counters; neither is inferred from a timestamp.
        lost_from = self.last + 1
        lost = first.collections - lost_from
        if lost <= 0:
            return None

        # Delta duration spans the records after `last` through this one, so
        # taking this one's own pause back out leaves the `lost` records alone.
        # The two come from different clocks, a cumulative float of seconds
        # against ns timestamps, so a gap holding almost no pause can subtract
        # to a hair below zero. Floor it: negative pause means nothing, and it
        # would drag `exact_pause_ns` under the sum gcmon measured.
        spanned_ns = secs_to_ns(first.duration - self.last_duration)
        return LossWindow(
            ts_start=max(self.last_ts_stop, confirmed_ts),
            ts_stop=first.ts_start,
            gen=first.gen,
            lost_from=lost_from,
            lost_count=lost,
            lost_pause_ns=max(0, spanned_ns - (first.ts_stop - first.ts_start)),
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
        total for the pause alone, but they partition it, so scaling a
        measured phase sum by this estimates it. Percentiles it cannot
        correct; see ADR-0015.
        """
        if self.sampled_pause_ns == 0:
            return 1.0
        return self.exact_pause_ns / self.sampled_pause_ns


def stack_order(windows: Iterable[LossWindow]) -> list[LossWindow]:
    """One poll's windows for one interpreter, in the order a stack can take
    them: ``ts_start`` ascending, then ``ts_stop`` descending.

    The windows are laminar before they are sorted, and nothing here reshapes
    them. Every window a poll opens for one interpreter starts at the same
    instant, because ``confirmed_by_interpreter`` takes the maximum
    ``last_ts_stop`` across that interpreter's rings and so dominates any one
    ring's own value: the windows differ only in where each generation's next
    observed record sits. A shared left edge with differing right edges nests,
    it cannot cross. Across polls they are disjoint, poll N+1 opening at or
    after the newest record poll N saw.

    Ordering is the whole job, and it is load-bearing. Slices on one Perfetto
    track are a stack, so an END closes the most recently opened slice: three
    BEGINs at one timestamp have to go out widest first or the first END
    closes the wrong span. A trace built the other way still parses and still
    renders — the trace processor reports ``misplaced_end_event = 0`` and
    reads the crossing as nesting — so nothing downstream would say a word.
    """
    return sorted(windows, key=lambda w: (w.ts_start, -w.ts_stop))


def to_loss_msg(iid: int, window: LossWindow) -> LossMsg:
    """The record the exporters carry, for one window of one generation."""
    return LossMsg(
        iid=iid,
        gen=window.gen,
        ts_start=window.ts_start,
        ts_stop=window.ts_stop,
        lost_from=window.lost_from,
        lost_count=window.lost_count,
        lost_pause_ns=window.lost_pause_ns,
    )


def confirmed_by_interpreter(cursors: Mapping[CursorKey, KeyAccumulator]) -> dict[int, int]:
    """The latest record seen anywhere in each interpreter so far.

    One bulk read covers all of an interpreter's generations, so a poll that
    found one key's counter unchanged proves nothing was lost on it up to that
    read, and the newest record any key returned bounds when that read
    happened. A gap found later cannot have opened before it.
    """
    confirmed: dict[int, int] = {}
    for (iid, _gen), accumulator in cursors.items():
        confirmed[iid] = max(confirmed.get(iid, 0), accumulator.last_ts_stop)

    return confirmed

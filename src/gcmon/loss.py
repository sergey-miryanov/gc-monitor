"""Reconstructing the GC records a poll could not read.

CPython writes one record per finished GC run, and a target whose collector
runs faster than gcmon polls loses records before any poll reads them. Two
cumulative fields make the loss measurable: ``collections`` counts what was
missed, and ``duration`` gives the pause time nobody saw.

``EventsMonitor`` owns one ``KeyAccumulator`` per ``(pid, iid, gen)``; see
ADR-0015 for why the spans need a track of their own.
"""

from collections.abc import Mapping, Sequence

import msgspec

from .data import GenLoss, LossMsg, secs_to_ns
from .protocol import TGCStatsInfo

__all__ = [
    "CursorKey",
    "KeyAccumulator",
    "KeyGap",
    "to_loss_msg",
]

# (iid, gen). One CPython ring buffer, with its own `collections` counter.
type CursorKey = tuple[int, int]


class KeyGap(msgspec.Struct):
    """A streak of records on one key that never reached gcmon, as counters.

    ``lost_from`` is the first of them, ``lost_count`` how many, and
    ``lost_pause_ns`` what the runs behind them cost together. All three come
    from subtracting two of the target's cumulative counters, so all three are
    exact. The far end is :func:`lost_to <gcmon.data.lost_to>`, derived from
    the other two.

    ``LossMsg`` carries the interval those runs happened in.
    """

    gen: int
    lost_from: int
    lost_count: int
    lost_pause_ns: int


class KeyAccumulator(msgspec.Struct):
    """What one key did, against what gcmon saw of it.

    ``last`` doubles as the poll cursor: a record whose ``collections`` does
    not exceed it has gone out already, or never arrived.
    """

    first_collections: int = 0
    first_pause_ns: int = 0
    first_duration: float = 0.0
    last_collections: int = 0
    last_duration: float = 0.0
    last_ts_stop: int = 0
    sampled_count: int = 0
    sampled_pause_ns: int = 0

    def observe_batch(self, events: Sequence[TGCStatsInfo]) -> KeyGap | None:
        """Fold the records one poll returned for this key, in counter order.

        A ring holds consecutive records, so only the first of them can sit
        across a gap and only the last settles the cursor. Returns the gap
        they sit behind, if any, for the caller to emit.

        They must be sorted by counter, past ``last``, and free of the copy
        the target makes of a record ahead of overwriting it; ``_ingest``
        guarantees all three. Contiguity it trusts without checking, see
        ADR-0015.
        """
        if not events:
            return None

        # The first record on a key opens no gap. Whatever ran before it is
        # outside the observed span, and gcmon cannot tell "ran before we
        # attached" from "lost".
        seeding = self.sampled_count == 0
        if seeding:
            self.first_collections = events[0].collections
            self.first_pause_ns = events[0].ts_stop - events[0].ts_start
            self.first_duration = events[0].duration

        gap = None if seeding else self._gap_before(events[0])

        for event in events:
            self.sampled_pause_ns += event.ts_stop - event.ts_start

        last = events[-1]
        self.sampled_count += len(events)
        self.last_collections = last.collections
        self.last_duration = last.duration
        self.last_ts_stop = last.ts_stop

        return gap

    def _gap_before(self, event: TGCStatsInfo) -> KeyGap | None:
        """Describe the gap sitting before this record, if there is one.

        Touches no running total; :meth:`observe_batch` owns those.
        """
        # `last` is the newest counter this key returned, so `last + 1` onward
        # up to the one before `first` never reached gcmon. Both fences are the
        # target's own counters; neither is inferred from a timestamp.
        lost_from = self.last_collections + 1
        lost = event.collections - lost_from
        if lost <= 0:
            return None

        # Delta duration spans the records after `last` through this one, so
        # taking this one's own pause back out leaves the `lost` records alone.
        # The two come from different clocks, a cumulative float of seconds
        # against ns timestamps, so a gap holding almost no pause can subtract
        # to a hair below zero. Floor it: negative pause means nothing, and it
        # would drag `exact_pause_ns` under the sum gcmon measured.
        spanned_ns = secs_to_ns(event.duration - self.last_duration)
        return KeyGap(
            gen=event.gen,
            lost_from=lost_from,
            lost_count=lost,
            lost_pause_ns=max(0, spanned_ns - (event.ts_stop - event.ts_start)),
        )

    @property
    def exact_count(self) -> int:
        """GC runs over the observed span, counting both ends.

        Whatever ran before the first observed record is outside the span:
        gcmon cannot tell "ran before we attached" from "lost".
        """
        if self.sampled_count == 0:
            return 0
        return self.last_collections - self.first_collections + 1

    @property
    def exact_pause_ns(self) -> int:
        """Pause time over the same span, from the target's own accumulator.

        ``first_duration`` is cumulative through the first observed record, so
        the delta starts after it. Adding that record's pause back is what
        makes this cover the runs ``exact_count`` counts.
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


def to_loss_msg(
    iid: int,
    ts_start: int,
    ts_stop: int,
    observed: Mapping[int, int],
    gaps: Mapping[int, KeyGap],
) -> LossMsg:
    """The record the exporters carry, for one poll on one interpreter.

    *observed* is how many records each generation returned over this interval
    and *gaps* what each of them lost, both keyed by generation. Every
    generation named in either gets an entry.
    """
    gens = sorted(observed.keys() | gaps.keys())
    return LossMsg(
        iid=iid,
        ts_start=ts_start,
        ts_stop=ts_stop,
        gens=[
            GenLoss(
                gen=gen,
                observed_count=observed.get(gen, 0),
                lost_from=gaps[gen].lost_from if gen in gaps else 0,
                lost_count=gaps[gen].lost_count if gen in gaps else 0,
                lost_pause_ns=gaps[gen].lost_pause_ns if gen in gaps else 0,
            )
            for gen in gens
        ],
    )

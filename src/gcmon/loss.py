"""Reconstructing the GC records a poll could not read.

CPython writes one record per finished GC run, and a target whose collector
runs faster than gcmon polls loses records before any poll reads them. Two
cumulative fields make the loss measurable: ``collections`` counts what was
missed, and ``duration`` gives the pause time nobody saw.

``EventsMonitor`` owns one ``RingAccumulator`` per ``(pid, iid, gen)``; see
ADR-0015 for why the spans need a track of their own.
"""

from collections.abc import Iterable, Sequence

import msgspec

from .data import GenLoss, secs_to_ns
from .protocol import TGCStatsInfo

__all__ = [
    "RingAccumulator",
    "RingKey",
]

# (iid, gen). One CPython ring buffer, with its own `collections` counter.
type RingKey = tuple[int, int]


class RingAccumulator(msgspec.Struct):
    """What one ring did, against what gcmon saw of it.

    ``last`` doubles as the poll cursor: a record whose ``collections`` does
    not exceed it has gone out already, or never arrived.
    """

    first_collections: int = 0
    first_pause_ns: int = 0
    first_duration: float = 0.0
    last_collections: int = 0
    last_duration: float = 0.0
    sampled_count: int = 0
    sampled_pause_ns: int = 0

    def unseen(self, events: Iterable[TGCStatsInfo]) -> list[TGCStatsInfo]:
        """The records in *events* this ring has not returned before, one per
        counter, in the order they arrived.

        *events* is one poll's records for this ring. A record whose
        ``collections`` does not exceed the cursor went out on an earlier
        poll.

        Two slots can report one counter, and they are one record rather than
        two. No threshold tells them apart, so the counter is what identifies
        a record. A dict keeps the last of such a pair, which a batch in slot
        order leaves in slot order. Which of the two survives cannot matter
        under the publishing contract ADR-0015 rests on, and would if that
        ever stopped holding: the last record of the batch sets
        ``last_duration``, the pause base the next poll subtracts from.
        """
        fresh = {event.collections: event for event in events if event.collections > self.last_collections}
        return list(fresh.values())

    def observe_batch(self, events: Sequence[TGCStatsInfo]) -> GenLoss | None:
        """Fold the records one poll returned for this ring, in counter order.

        A ring holds consecutive records, so only the first of them can sit
        across a gap and only the last settles the cursor. Returns this
        generation's entry for the poll, which the caller hangs on a
        ``LossMsg`` as it stands, or ``None`` when the poll returned nothing
        to fold.

        They must be sorted by counter, which is what :meth:`unseen` preserves
        and ``_ingest`` provides, and past ``last`` and free of duplicates,
        which is what :meth:`unseen` returns. Contiguity it trusts without
        checking, see ADR-0015.
        """
        if not events:
            return None

        # The first record on a ring opens no gap. Whatever ran before it is
        # outside the observed span, and gcmon cannot tell "ran before we
        # attached" from "lost".
        seeding = self.sampled_count == 0
        if seeding:
            self.first_collections = events[0].collections
            self.first_pause_ns = events[0].ts_stop - events[0].ts_start
            self.first_duration = events[0].duration
            entry = GenLoss(gen=events[0].gen, observed_count=len(events))
        else:
            entry = self._gen_loss(events[0], len(events))

        for event in events:
            self.sampled_pause_ns += event.ts_stop - event.ts_start

        last = events[-1]
        self.sampled_count += len(events)
        self.last_collections = last.collections
        self.last_duration = last.duration

        return entry

    def _gen_loss(self, first: TGCStatsInfo, observed_count: int) -> GenLoss:
        """This ring's entry for one poll: *observed_count* records read, and
        the records missing ahead of *first*, if there are any.

        Touches no running total; :meth:`observe_batch` owns those.
        """
        # `last` is the newest counter this ring returned, so `last + 1` onward
        # up to the one before `first` never reached gcmon. Both fences are the
        # target's own counters; neither is inferred from a timestamp.
        lost_from = self.last_collections + 1
        lost = first.collections - lost_from
        if lost <= 0:
            return GenLoss(gen=first.gen, observed_count=observed_count)

        # Delta duration spans the records after `last` through this one, so
        # taking this one's own pause back out leaves the `lost` records alone.
        # The two come from different clocks, a cumulative float of seconds
        # against ns timestamps, so a gap holding almost no pause can subtract
        # to a hair below zero. Floor it: negative pause means nothing, and it
        # would drag `exact_pause_ns` under the sum gcmon measured.
        spanned_ns = secs_to_ns(first.duration - self.last_duration)
        return GenLoss(
            gen=first.gen,
            observed_count=observed_count,
            lost_count=lost,
            lost_pause_ns=max(0, spanned_ns - (first.ts_stop - first.ts_start)),
            lost_from=lost_from,
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

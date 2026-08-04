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
    "confirmed_by_interpreter",
    "merge_windows",
    "split_around",
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

    def observe_batch(self, events: Sequence[TGCStatsInfo], confirmed_ts: int = 0) -> LossWindow | None:
        """Fold one poll's run of records for this key, in counter order.

        A ring holds consecutive records, so only the run's first record can
        sit across a gap and only its last one settles the cursor. Returns
        the window that gap opened, if any, for the caller to merge and emit.

        *confirmed_ts* is the latest record seen anywhere in this interpreter
        before this poll. A poll that found the counter unchanged proves
        nothing was lost up to that read, so the window cannot start earlier.

        The run must be sorted by counter, past ``last``, and free of the
        copy the target makes of a record ahead of overwriting it; ``_ingest``
        guarantees all three. Contiguity is trusted rather than checked, see
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

        lost = first.collections - self.last - 1
        if lost <= 0:
            return None

        # Delta duration spans the records after `last` through this one, so
        # taking this one's own pause back out leaves the `lost` records alone.
        # The two come from different clocks — a cumulative float of seconds
        # against ns timestamps — so a gap holding almost no pause can subtract
        # to a hair below zero. Floor it: negative pause has no meaning, and it
        # would otherwise drag `exact_pause_ns` under the sum gcmon measured.
        spanned_ns = secs_to_ns(first.duration - self.last_duration)
        return LossWindow(
            ts_start=max(self.last_ts_stop, confirmed_ts),
            ts_stop=first.ts_start,
            gen=first.gen,
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
        total for the pause alone, but they partition it — so scaling a
        measured phase sum by this estimates it. Percentiles it cannot
        correct; see ADR-0015.
        """
        if self.sampled_pause_ns == 0:
            return 1.0
        return self.exact_pause_ns / self.sampled_pause_ns


def merge_windows(windows: Iterable[LossWindow]) -> list[MergedLoss]:
    """Collapse one poll's windows for one interpreter into disjoint spans.

    Windows on one key never overlap, being consecutive gaps in one sequence,
    but windows from different generations cross whenever the records bounding
    them interleave. Merging keeps their shared track laminar without clipping
    a span the way ADR-0011's sweep has to.

    One poll is the whole of it. A single bulk read gives every generation of
    an interpreter the same confirmation point, so windows opened in later
    polls start after these end and can never reach back into them.

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


def _apportion(total: int, weights: Sequence[int]) -> list[int]:
    """Share *total* out in proportion to *weights*, largest remainder first.

    The parts add back up to *total* exactly, so splitting a span never
    invents or loses a collection.
    """
    span = sum(weights)
    if total <= 0 or span <= 0:
        return [0] * len(weights)

    parts = [total * weight // span for weight in weights]
    order = sorted(range(len(weights)), key=lambda i: (total * weights[i]) % span, reverse=True)
    for i in order[: total - sum(parts)]:
        parts[i] += 1

    return parts


def _cut(ts_start: int, ts_stop: int, observed: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """``[ts_start, ts_stop]`` with each observed interval removed from it."""
    pieces: list[tuple[int, int]] = []
    cursor = ts_start

    for obs_start, obs_stop in sorted(observed):
        if obs_stop <= cursor or obs_start >= ts_stop:
            continue
        if obs_start > cursor:
            pieces.append((cursor, obs_start))
        cursor = obs_stop
        if cursor >= ts_stop:
            return pieces

    pieces.append((cursor, ts_stop))
    return pieces


def split_around(span: MergedLoss, observed: Iterable[tuple[int, int]]) -> list[MergedLoss]:
    """Cut *span* into the stretches where gcmon was actually blind.

    A collection observed inside a span is one the lost records cannot have
    run during — collections in an interpreter are serialized — so the span
    owes it a hole. What is left is where the missing records must be: a
    gen-0 window bracketing an observed gen-1 collection becomes two pieces,
    one either side of it, instead of one bar drawn over the top of it.

    Counts and pause are shared across the pieces in proportion to width.
    Nothing in the ring says where inside the span the records ran, so no
    split is more true than another — but proportional is the one that adds
    back up, and it keeps a piece from claiming more pause than it has room
    for. A piece left holding nothing is dropped rather than drawn as a bar
    reporting no loss.
    """
    pieces = _cut(span.ts_start, span.ts_stop, observed)
    if not pieces:
        # An observation covers the span end to end, leaving nowhere it could
        # have been blind. Drawing the bar anyway would put it on top of a
        # collection gcmon watched; the totals are recorded either way.
        return []
    if len(pieces) == 1:
        span.ts_start, span.ts_stop = pieces[0]
        return [span]

    widths = [stop - start for start, stop in pieces]
    out = [MergedLoss(ts_start=start, ts_stop=stop) for start, stop in pieces]
    kept: set[int] = set()

    for gen, count in span.lost_count.items():
        # Pause follows the records, not the clock: a piece holding none of
        # the collections holds none of their pause either, and is dropped
        # rather than drawn as a bar reporting no loss.
        shares = _apportion(count, widths)
        pauses = _apportion(span.lost_pause_ns.get(gen, 0), shares)
        for i, (share, pause) in enumerate(zip(shares, pauses, strict=True)):
            if share or pause:
                out[i].lost_count[gen] = share
                out[i].lost_pause_ns[gen] = pause
                kept.add(i)

    return [piece for i, piece in enumerate(out) if i in kept]


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


def confirmed_by_interpreter(cursors: Mapping[CursorKey, KeyAccumulator]) -> dict[int, int]:
    """The latest record seen anywhere in each interpreter so far.

    One bulk read covers all of an interpreter's generations, so a poll that
    found one key's counter unchanged proves nothing was lost on it up to that
    read — and the newest record any key returned bounds when that read
    happened. A gap found later cannot have opened before it.
    """
    confirmed: dict[int, int] = {}
    for (iid, _gen), accumulator in cursors.items():
        confirmed[iid] = max(confirmed.get(iid, 0), accumulator.last_ts_stop)

    return confirmed

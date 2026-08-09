"""Tests for reconstructing what a poll could not observe.

Two kinds of input here. The synthetic runs below carry a cumulative
``duration`` the way a real target does, so they can check the arithmetic
against ground truth: build a full run, show the accumulator only what
survives a ring of a given size, and compare what it reconstructs to what
actually happened. The capture fixture from ``test_monitor_cursor`` carries
no durations, so it checks gap counts against real slot data instead.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from itertools import groupby, pairwise
from typing import override

import msgspec.structs
import pytest

from gcmon.data import GCStatsInfo, lost_to, secs_to_ns
from gcmon.exporters.exporter import EventsExporter
from gcmon.loss import (
    KeyAccumulator,
    LossWindow,
    confirmed_by_interpreter,
    stack_order,
    to_loss_msg,
)
from gcmon.monitor import EventsMonitor
from gcmon.protocol import TGCStatsInfo, TInstantMsg, TLossMsg
from gcmon.stats import StreamingStats
from gcmon.target_process import ExternalProcess
from tests.helpers import create_mock_stats_item
from tests.test_monitor_cursor import POLL_0, POLL_1, build_batch

TS0 = 1_000_000_000
SPACING_NS = 1_150_000  # measured gap between gen-0 collections

# (gen, ts_start, ts_stop) triples a poll can actually produce, given that its
# windows for one interpreter share a left edge and later polls open after
# earlier ones close: nothing, one window, three generations on one edge and
# the same shuffled, two of equal width, three polls in a row, and a
# zero-length pair.
SHAPES: list[list[tuple[int, int, int]]] = [
    [],
    [(0, 10, 20)],
    [(0, 10, 40), (1, 10, 30), (2, 10, 20)],
    [(2, 10, 20), (0, 10, 40), (1, 10, 30)],
    [(0, 10, 20), (1, 10, 20)],
    [(0, 10, 20), (1, 10, 15), (0, 30, 60), (1, 30, 45), (2, 80, 90)],
    [(0, 5, 5), (1, 5, 5)],
]


def varied_pause(n: int) -> int:
    """Pause lengths that do not all match, so a sum cannot pass by accident."""
    return 100_000 + (n % 7) * 13_000


def build_run(
    count: int,
    gen: int = 0,
    iid: int = 0,
    pause_ns: Callable[[int], int] = varied_pause,
    spacing_ns: int = SPACING_NS,
    ts0: int = TS0,
    first_collection: int = 1,
) -> list[GCStatsInfo]:
    """Every collection a target performs, with ``duration`` accumulating."""
    events: list[GCStatsInfo] = []
    cumulative_ns = 0
    ts = ts0

    for nth in range(count):
        collections = first_collection + nth
        pause = pause_ns(collections)
        cumulative_ns += pause
        events.append(
            create_mock_stats_item(
                gen=gen,
                iid=iid,
                collections=collections,
                ts_start=ts,
                ts_stop=ts + pause,
                duration=cumulative_ns / 1e9,
            )
        )
        ts += spacing_ns

    return events


def ring_polls(events: Sequence[GCStatsInfo], capacity: int, per_tick: int) -> Iterator[Sequence[GCStatsInfo]]:
    """What a monitor reads: at each tick, the newest *capacity* records done so far."""
    for end in range(per_tick, len(events) + 1, per_tick):
        yield events[max(0, end - capacity) : end]


RING_CAPACITY = {0: 11, 1: 3, 2: 3}  # GC_YOUNG_STATS_SIZE, then GC_OLD_STATS_SIZE twice

# An older generation walks more of the heap. `varied_pause` gives gen 0 the
# ~100-180 us the capture measured, so gen 1 pauses for about a millisecond and
# a full collection for tens of them, which is the shape that matters here: a
# gen-0 window bracketing an observed gen-2 collection is mostly that
# collection, leaving the lost records far less room than the window is wide.
PAUSE_SCALE = {0: 1, 1: 8, 2: 300}


def build_interleaved_run(count: int, iid: int = 0, gap_ns: int = 900_000, ts0: int = TS0) -> list[GCStatsInfo]:
    """Every collection one interpreter performs, in the order they ran.

    CPython serializes collections within an interpreter, so the three
    generations' records interleave without ever overlapping in time. Each
    generation carries its own ``collections`` counter and its own cumulative
    ``duration``, runs a tenth as often as the one below it, and pauses for
    longer by ``PAUSE_SCALE``.
    """
    events: list[GCStatsInfo] = []
    counters = {0: 0, 1: 0, 2: 0}
    cumulative = {0: 0, 1: 0, 2: 0}
    ts = ts0

    for nth in range(count):
        gen = 2 if nth % 100 == 99 else 1 if nth % 10 == 9 else 0
        counters[gen] += 1
        pause = varied_pause(counters[gen]) * PAUSE_SCALE[gen]
        cumulative[gen] += pause
        events.append(
            create_mock_stats_item(
                gen=gen,
                iid=iid,
                collections=counters[gen],
                ts_start=ts,
                ts_stop=ts + pause,
                duration=cumulative[gen] / 1e9,
            )
        )
        ts += pause + gap_ns

    return events


def interpreter_polls(
    events: Sequence[GCStatsInfo], per_tick: int, capacity: Mapping[int, int] = RING_CAPACITY
) -> Iterator[Sequence[GCStatsInfo]]:
    """What one bulk read returns: every generation's ring at once.

    Each ring holds the newest *capacity* records of its own generation to
    have finished by that tick, so the generations lose records over stretches
    that cross and the windows merge, which ``ring_polls`` on a single-key run
    never produces.
    """
    for end in range(per_tick, len(events) + per_tick, per_tick):
        done = events[:end]
        batch: list[GCStatsInfo] = []
        for gen, slots in capacity.items():
            batch.extend([event for event in done if event.gen == gen][-slots:])
        yield batch


def fold_singly(events: Sequence[GCStatsInfo]) -> KeyAccumulator:
    """The same records, one single-record run at a time."""
    accumulator = KeyAccumulator()
    for event in events:
        accumulator.observe_batch([event])
    return accumulator


class Ingested:
    """What a sequence of polls left behind.

    Mirrors ``EventsMonitor._ingest``, which emits each poll's windows rather
    than retaining them, so a test that wants to look at a window has to
    collect it on the way past. ``observed`` retains the same way, standing in
    for the ``fresh`` list ``_ingest`` hands to the exporter: those are the
    records that become drawn ``GC Pause`` slices, and the partition needs to
    know which counters they were.

    ``windows`` holds every window a poll measured, drawable or not, because
    that is what ``_ingest`` hands to ``StreamingStats.record_loss``.
    ``undrawable`` holds the ones it then held back, and ``spans`` draws the
    rest.
    """

    def __init__(self) -> None:
        self.cursors: dict[tuple[int, int], KeyAccumulator] = {}
        self.windows: dict[tuple[int, int], list[LossWindow]] = {}
        self.undrawable: dict[tuple[int, int], list[LossWindow]] = {}
        self.observed: dict[tuple[int, int], list[int]] = {}

    def poll(self, batch: Sequence[GCStatsInfo]) -> dict[int, list[LossWindow]]:
        """Fold one whole ring buffer; return the windows it opened, by iid.

        Slot order is not time order, so walking the batch as it came would
        seed ``first`` from whichever record sat at the ring's write position.
        Sort each ring back into counter order, drop what the cursor has
        already passed, and hand the rest over as one run.
        """
        confirmed = confirmed_by_interpreter(self.cursors)

        ordered = sorted(
            (event for event in batch if event.ts_start < event.ts_stop),
            key=lambda e: (e.iid, e.gen, e.collections),
        )

        opened: dict[int, list[LossWindow]] = {}
        for key, group in groupby(ordered, key=lambda e: (e.iid, e.gen)):
            accumulator = self.cursors.setdefault(key, KeyAccumulator())
            seen = accumulator.last
            run = list({event.collections: event for event in group if event.collections > seen}.values())

            window = accumulator.observe_batch(run, confirmed.get(key[0], 0))
            if window is not None:
                # `_ingest` records the loss here, then draws the window only
                # if its bounds describe an interval. Mirror both halves: the
                # measurement is unconditional, the span is not.
                self.windows.setdefault(key, []).append(window)
                if window.is_drawable:
                    opened.setdefault(key[0], []).append(window)
                else:
                    self.undrawable.setdefault(key, []).append(window)
            # `_ingest` does `fresh.extend(run)` here, and every record in
            # `fresh` is drawn.
            self.observed.setdefault(key, []).extend(event.collections for event in run)

        return opened

    def spans(self, iid: int = 0) -> list[LossWindow]:
        """What `_ingest` would draw: this interpreter's windows, each as
        itself, in the order the loss track's stack can take them.

        `_ingest` orders one poll's windows at a time. Sorting every poll's at
        once gives the same sequence, since a poll opens its windows at or
        after the newest record the poll before it saw, and that is at or
        after every window that poll closed.
        """
        windows = [w for key, ws in self.windows.items() if key[0] == iid for w in ws if w.is_drawable]
        return stack_order(windows)

    def __getitem__(self, key: tuple[int, int]) -> KeyAccumulator:
        return self.cursors[key]

    def windows_for(self, key: tuple[int, int]) -> list[LossWindow]:
        return self.windows.get(key, [])

    def measured(self, iid: int = 0) -> list[LossWindow]:
        """Every window this interpreter's polls measured, drawn or not."""
        return [w for key, ws in self.windows.items() if key[0] == iid for w in ws]

    def undrawable_count(self, iid: int = 0) -> int:
        """What `StreamingStats.undrawable_count` would hold for *iid*."""
        return sum(len(ws) for key, ws in self.undrawable.items() if key[0] == iid)

    def observed_for(self, key: tuple[int, int]) -> list[int]:
        """The counters of the records that reached the exporter on this key."""
        return self.observed.get(key, [])


def observe_all(batches: Iterable[Sequence[GCStatsInfo]]) -> Ingested:
    ingested = Ingested()
    for batch in batches:
        ingested.poll(batch)
    return ingested


def true_pause_ns(events: Sequence[GCStatsInfo], first: int, last: int) -> int:
    """Ground truth: the pause sum over collections *first* through *last*."""
    return sum(e.ts_stop - e.ts_start for e in events if first <= e.collections <= last)


def window(
    gen: int = 0,
    ts_start: int = 0,
    ts_stop: int = 0,
    lost_count: int = 1,
    lost_pause_ns: int = 0,
    lost_from: int = 1,
) -> LossWindow:
    return LossWindow(
        ts_start=ts_start,
        ts_stop=ts_stop,
        gen=gen,
        lost_from=lost_from,
        lost_count=lost_count,
        lost_pause_ns=lost_pause_ns,
    )


def depths(windows: Sequence[LossWindow]) -> list[int]:
    """Walk an emission order as the loss track does, and report each span's
    nesting depth.

    A track is a stack: a span opens on top of whatever is still open and an
    END closes the topmost. Raises for an order the track cannot express — a
    span that neither nests inside the one still open nor starts after it
    closes — which is the failure that leaves the trace parsing fine and
    every span reparented.
    """
    stack: list[LossWindow] = []
    found: list[int] = []

    for w in windows:
        while stack and stack[-1].ts_stop <= w.ts_start:
            stack.pop()
        if stack:
            assert w.ts_stop <= stack[-1].ts_stop, f"{w} crosses {stack[-1]}"
        found.append(len(stack))
        stack.append(w)

    return found


@pytest.fixture
def accumulator() -> KeyAccumulator:
    return KeyAccumulator()


@pytest.fixture
def captured() -> Ingested:
    """The verbatim two-poll capture, ingested the way the monitor would."""
    return observe_all([build_batch(POLL_0), build_batch(POLL_1)])


class TestEmptyAccumulator:
    def test_reports_nothing(self, accumulator: KeyAccumulator) -> None:
        assert accumulator.exact_count == 0
        assert accumulator.exact_pause_ns == 0
        assert accumulator.lost_count == 0

    def test_coverage_and_scale_are_neutral(self, accumulator: KeyAccumulator) -> None:
        """Nothing observed and nothing lost. Returning 1.0 rather than
        raising keeps a division out of every call site."""
        assert accumulator.coverage == 1.0
        assert accumulator.scale_factor == 1.0

    def test_last_starts_below_every_counter(self, accumulator: KeyAccumulator) -> None:
        """``last`` doubles as the poll cursor, and CPython counts from 1."""
        assert accumulator.last == 0


class TestFencepost:
    def test_one_record_spans_itself(self, accumulator: KeyAccumulator) -> None:
        window = accumulator.observe_batch(
            [create_mock_stats_item(collections=42, ts_start=1_000, ts_stop=1_700, duration=0.0007)]
        )

        assert accumulator.exact_count == 1
        assert accumulator.exact_pause_ns == 700
        assert accumulator.sampled_pause_ns == 700
        assert window is None

    def test_two_adjacent_records_leave_no_gap(self, accumulator: KeyAccumulator) -> None:
        window = accumulator.observe_batch(build_run(2))

        assert accumulator.exact_count == 2
        assert accumulator.lost_count == 0
        assert window is None

    def test_exact_pause_covers_the_first_record(self, accumulator: KeyAccumulator) -> None:
        """The delta of a cumulative field starts *after* the first record, so
        dropping the fencepost term would under-report by one pause."""
        events = build_run(5)
        accumulator.observe_batch(events)

        assert accumulator.exact_pause_ns == true_pause_ns(events, 1, 5)
        assert accumulator.exact_pause_ns != secs_to_ns(events[-1].duration - events[0].duration)

    def test_a_span_starting_late_ignores_earlier_collections(self, accumulator: KeyAccumulator) -> None:
        """gcmon cannot tell "ran before we attached" from "lost", so
        collections before the first observed record are outside the span."""
        events = build_run(20)
        accumulator.observe_batch(events[10:])

        assert accumulator.exact_count == 10
        assert accumulator.exact_pause_ns == true_pause_ns(events, 11, 20)


class TestGapDetection:
    """Gaps open at the seam between two polls, so every case here folds one
    run, then another that starts further along than the first ended."""

    def test_a_skipped_record_opens_a_window(self, accumulator: KeyAccumulator) -> None:
        events = build_run(3)
        accumulator.observe_batch([events[0]])
        gap = accumulator.observe_batch([events[2]])

        assert gap is not None
        assert gap.lost_count == 1
        assert gap.lost_pause_ns == events[1].ts_stop - events[1].ts_start

    def test_the_window_names_the_collections_it_is_missing(self, accumulator: KeyAccumulator) -> None:
        """The gap is found by subtracting the ring's own counters, so both
        bounds are in hand before the count is. Records 2, 3 and 4 were
        overwritten: the window says so rather than saying "three of them"."""
        events = build_run(6)
        accumulator.observe_batch([events[0]])
        gap = accumulator.observe_batch([events[4]])

        assert gap is not None
        assert (gap.lost_from, gap.lost_count) == (2, 3)
        assert lost_to(gap.lost_from, gap.lost_count) == 4
        assert [e.collections for e in events[1:4]] == [2, 3, 4]

    def test_the_range_stops_short_of_the_records_that_bound_it(self, accumulator: KeyAccumulator) -> None:
        """Both fences, in the smallest case that has them: the record before
        the gap and the record after it were observed and are drawn, so a range
        reaching either would charge a collection twice."""
        events = build_run(3)
        accumulator.observe_batch([events[0]])
        gap = accumulator.observe_batch([events[2]])

        assert gap is not None
        assert gap.lost_from == events[0].collections + 1
        assert lost_to(gap.lost_from, gap.lost_count) == events[2].collections - 1

    def test_the_window_is_bounded_by_observed_records(self, accumulator: KeyAccumulator) -> None:
        events = build_run(6)
        accumulator.observe_batch([events[0]])
        gap = accumulator.observe_batch([events[4]])

        assert gap is not None
        assert gap.ts_start == events[0].ts_stop
        assert gap.ts_stop == events[4].ts_start

    def test_a_pause_shortfall_floors_at_zero(self, accumulator: KeyAccumulator) -> None:
        """``duration`` is a cumulative float of seconds while the bounds are
        ns timestamps, so a gap holding almost no pause can subtract to a hair
        below zero. Negative pause has no meaning downstream: it would drag
        the exact sum under the one gcmon measured, and make the scale factor
        shrink what it exists to grow."""
        first = create_mock_stats_item(gen=0, collections=1, ts_start=TS0, ts_stop=TS0 + 100_000, duration=100e-6)
        # Record 2 is lost. Record 3's own pause is 200 us, but the target's
        # accumulator has only moved 199 us since record 1.
        third = create_mock_stats_item(
            gen=0, collections=3, ts_start=TS0 + 10_000_000, ts_stop=TS0 + 10_200_000, duration=299e-6
        )

        accumulator.observe_batch([first])
        gap = accumulator.observe_batch([third])

        assert gap is not None
        assert gap.lost_pause_ns == 0

    def test_the_window_carries_its_generation(self, accumulator: KeyAccumulator) -> None:
        events = build_run(3, gen=1)
        accumulator.observe_batch([events[0]])
        gap = accumulator.observe_batch([events[2]])

        assert gap is not None
        assert gap.gen == 1

    def test_a_lossless_run_opens_none(self, accumulator: KeyAccumulator) -> None:
        assert accumulator.observe_batch(build_run(50)) is None
        assert accumulator.coverage == 1.0
        assert accumulator.scale_factor == pytest.approx(1.0, abs=1e-9)

    def test_no_window_before_the_first_record_or_after_the_last(self, accumulator: KeyAccumulator) -> None:
        events = build_run(30)
        assert accumulator.observe_batch(events[10:20]) is None


class TestObserveBatch:
    """A poll hands over one ring's run at once. Whatever that saves, it has
    to leave the accumulator where folding the same records one at a time
    would have left it."""

    @pytest.mark.parametrize("count", [1, 2, 11])
    def test_a_run_matches_record_by_record(self, count: int) -> None:
        events = build_run(count)
        batched = KeyAccumulator()

        batched.observe_batch(events)

        assert batched == fold_singly(events)

    def test_an_empty_run_changes_nothing(self, accumulator: KeyAccumulator) -> None:
        accumulator.observe_batch([])

        assert accumulator == KeyAccumulator()

    def test_consecutive_polls_pick_up_where_the_last_left_off(self) -> None:
        events = build_run(20)
        batched = KeyAccumulator()

        batched.observe_batch(events[:11])
        batched.observe_batch(events[11:])

        assert batched == fold_singly(events)

    def test_a_gap_between_two_runs_opens_a_window(self) -> None:
        """The seam between polls is where a ring loses records, and the only
        place a contiguous run can have lost any."""
        events = build_run(20)
        batched = KeyAccumulator()

        batched.observe_batch(events[:5])
        gap = batched.observe_batch(events[12:])

        assert gap is not None
        assert gap.lost_count == 7
        assert batched == fold_singly(events[:5] + events[12:])

    def test_a_hole_inside_a_run_goes_unnoticed(self) -> None:
        """Pinning an accepted risk, not a wanted behaviour. A run is trusted
        to be contiguous because a ring holds consecutive records; only a read
        torn by two collections landing inside one ~1 KB copy could break that.
        The ends still give the right counts, but nothing carries the hole's
        pause, so ADR-0015's invariant does not hold."""
        events = build_run(10)
        torn = events[:4] + events[6:]
        batched = KeyAccumulator()

        assert batched.observe_batch(torn) is None
        assert batched.lost_count == 2
        assert batched.exact_pause_ns > batched.sampled_pause_ns


class TestReconstructionAgainstGroundTruth:
    """Show the accumulator a lossy view; compare what it reports to what happened."""

    @pytest.mark.parametrize(
        ("capacity", "per_tick"),
        [
            (11, 87),  # gen 0, as measured
            (3, 8),  # gen 1, as measured
            (1, 5),  # free-threaded: one slot per generation
            (11, 11),  # exactly keeping up
            (11, 3),  # polling faster than the target collects
        ],
    )
    def test_counts_and_pause_sums_are_exact(self, capacity: int, per_tick: int) -> None:
        events = build_run(400)
        acc = observe_all(ring_polls(events, capacity, per_tick))[(0, 0)]

        assert acc.exact_count == acc.last - acc.first + 1
        assert acc.exact_pause_ns == true_pause_ns(events, acc.first, acc.last)

    @pytest.mark.parametrize(("capacity", "per_tick"), [(11, 87), (3, 8), (1, 5), (11, 11)])
    def test_the_invariant_holds(self, capacity: int, per_tick: int) -> None:
        """Exact pause time is what gcmon saw plus what every window says it
        missed. This is the one assertion that catches a fencepost error, a
        clock mismatch between ``duration`` and the timestamps, and a wrong
        window in a single check."""
        ingested = observe_all(ring_polls(build_run(400), capacity, per_tick))
        acc = ingested[(0, 0)]

        lost = sum(w.lost_pause_ns for w in ingested.windows_for((0, 0)))
        assert acc.exact_pause_ns == acc.sampled_pause_ns + lost

    def test_coverage_approaches_the_ring_ratio(self) -> None:
        """11 slots against 87 collections per tick keeps 11 of every 87, once
        the run is long enough to drown the first tick. That one is narrower:
        its span starts at the oldest slot still in the ring, so the 76
        records lost before gcmon ever looked fall outside the span."""
        acc = observe_all(ring_polls(build_run(8_700), 11, 87))[(0, 0)]

        assert acc.coverage == pytest.approx(11 / 87, rel=0.02)
        assert acc.coverage == acc.sampled_count / acc.exact_count

    def test_lost_count_matches_the_windows(self) -> None:
        ingested = observe_all(ring_polls(build_run(400), 11, 87))

        assert ingested[(0, 0)].lost_count == sum(w.lost_count for w in ingested.windows_for((0, 0)))

    def test_scale_factor_corrects_a_sampled_sum(self) -> None:
        acc = observe_all(ring_polls(build_run(400), 11, 87))[(0, 0)]

        corrected = acc.sampled_pause_ns * acc.scale_factor
        assert corrected == pytest.approx(acc.exact_pause_ns, rel=1e-9)

    @pytest.mark.parametrize(("capacity", "per_tick"), [(11, 87), (3, 8), (1, 5), (11, 11)])
    def test_every_window_holds_the_pause_it_reports(self, capacity: int, per_tick: int) -> None:
        """A window narrower than the collections it says ran inside it says
        something that cannot be true. Bounding it by this key's own records
        is what rules that out."""
        ingested = observe_all(ring_polls(build_run(400), capacity, per_tick))

        for w in ingested.windows_for((0, 0)):
            assert w.lost_pause_ns <= w.ts_stop - w.ts_start

    @pytest.mark.parametrize(("capacity", "per_tick"), [(11, 87), (3, 8), (1, 5), (11, 11)])
    def test_the_drawn_spans_carry_the_whole_loss(self, capacity: int, per_tick: int) -> None:
        """Drawing is a picture decision, so it must not change the totals."""
        ingested = observe_all(ring_polls(build_run(400), capacity, per_tick))

        spans = ingested.spans()
        assert sum(s.lost_count for s in spans) == ingested[(0, 0)].lost_count
        assert sum(s.lost_pause_ns for s in spans) == sum(w.lost_pause_ns for w in ingested.windows_for((0, 0)))


# (gap between collections, collections per tick). The first pace is the
# capture's, gen 0 collecting every ~1 ms against a 100 ms tick. The last two
# are a GC-bound target, two thirds of its wall time inside gen 0, which is
# what it takes for a blind interval to be nearly full of pause. That regime
# is the one the split misplaced pause in, and the one the loss track exists
# for: a ring only overflows when collections come faster than polls.
PACES = [(900_000, 40), (900_000, 87), (900_000, 400), (80_000, 87), (80_000, 120)]


class TestNoSpanOverstatesItsPause:
    """A bar cannot hold more GC than the interval it covers.

    Over a whole synthesised interpreter rather than a fixture, because this
    is a property of every bar the track draws, not of one shape: a span
    reporting 2 lost collections and 95 ms of lost pause across 90 ms is not
    uncertain, it is impossible, and the split produced exactly that whenever
    a piece was dropped for taking a zero share by width.

    It holds because every number on a span comes from the target's counters
    over the span's own bounds. A window's pause is what the target collected
    between two of that key's own records, and collections in an interpreter
    are serialized, so nothing is charged twice and all of it fits.
    """

    @pytest.mark.parametrize(("gap_ns", "per_tick"), PACES)
    def test_every_span_has_room_for_the_pause_it_reports(self, gap_ns: int, per_tick: int) -> None:
        run = build_interleaved_run(2_000, gap_ns=gap_ns)

        spans = observe_all(interpreter_polls(run, per_tick)).spans()

        assert spans
        for span in spans:
            assert span.lost_pause_ns <= span.ts_stop - span.ts_start

    @pytest.mark.parametrize(("gap_ns", "per_tick"), PACES)
    def test_no_span_is_drawn_reporting_nothing(self, gap_ns: int, per_tick: int) -> None:
        """Every bar on the track stands for records the counters say went
        missing. A span carrying none claims a stretch was blind while saying
        gcmon has no evidence anything happened in it."""
        run = build_interleaved_run(2_000, gap_ns=gap_ns)

        spans = observe_all(interpreter_polls(run, per_tick)).spans()

        assert all(span.lost_count > 0 for span in spans)

    @pytest.mark.parametrize(("gap_ns", "per_tick"), PACES)
    def test_the_emitted_order_is_a_stack(self, gap_ns: int, per_tick: int) -> None:
        """Over a whole synthesised interpreter, not one shape. The generations
        share a row, the row is a stack, and an order that crosses still parses
        and still renders — so nothing but this says a word about it."""
        run = build_interleaved_run(2_000, gap_ns=gap_ns)

        spans = observe_all(interpreter_polls(run, per_tick)).spans()

        assert max(depths(spans)) > 0

    def test_the_run_nests_two_generations_at_one_instant(self) -> None:
        """Otherwise the check above would only ever see spans at depth 0,
        where any order at all is a stack, and the ordering would go untested
        where it matters."""
        spans = observe_all(interpreter_polls(build_interleaved_run(2_000), 87)).spans()

        shared = [(a, b) for a, b in pairwise(spans) if a.ts_start == b.ts_start]
        assert shared
        assert all(a.gen != b.gen and a.ts_stop >= b.ts_stop for a, b in shared)

    def test_a_span_reaches_over_a_collection_gcmon_observed(self) -> None:
        """The consequence accepted in exchange. The span bounds where the
        missing records are; the observed collection is drawn on the
        interpreter's own row above, so a reader narrows it from there."""
        batches = list(interpreter_polls(build_interleaved_run(2_000), 87))
        observed = {(e.gen, e.collections): e for batch in batches for e in batch}

        spans = observe_all(batches).spans()

        assert any(s.ts_start < e.ts_start and e.ts_stop < s.ts_stop for s in spans for e in observed.values())


def charges(ingested: Ingested, key: tuple[int, int]) -> Counter[int]:
    """Every collection the trace claims on one ring, and how often.

    A reader adding a ring up has two sources: the ``GC Pause`` slices, one
    per record that reached the exporter, and the ``GC Loss`` spans, each
    naming a range of counters. Charging a counter to both, or to two spans,
    double-counts it; charging it to neither loses it with nothing on screen
    to say so.
    """
    charged: Counter[int] = Counter(ingested.observed_for(key))
    for w in ingested.windows_for(key):
        charged.update(range(w.lost_from, lost_to(w.lost_from, w.lost_count) + 1))
    return charged


class TestTheRingSpanIsPartitioned:
    """Every collection between the first and last gcmon observed on a ring is
    either drawn as a ``GC Pause`` slice or inside exactly one loss span's
    range. No collection twice, none unaccounted for.

    This is what ``lost_from`` buys, and it is the strongest statement
    available about the loss arithmetic: the counts alone can only be checked
    against themselves, whereas a partition is checked against the collections
    the target actually performed. A fencepost anywhere — the near fence of a
    range, its far fence, the count it was cut to — shows up here as an
    overlap or a hole, and nothing else in the suite would notice.

    Over a whole synthesised interpreter across several paces rather than one
    fixture, because the property is about every ring under every rate: the
    three generations lose records over stretches that cross, and only a run
    that fast makes a ring wrap at all.
    """

    @pytest.mark.parametrize(("gap_ns", "per_tick"), PACES)
    def test_every_collection_is_accounted_for_exactly_once(self, gap_ns: int, per_tick: int) -> None:
        run = build_interleaved_run(2_000, gap_ns=gap_ns)

        ingested = observe_all(interpreter_polls(run, per_tick))

        for (iid, gen), acc in ingested.cursors.items():
            # Ground truth: the collections the target really performed on this
            # ring, over the stretch gcmon can speak for. What ran before the
            # first observed record is outside the span, since nothing tells
            # "ran before we attached" from "lost".
            truth = {e.collections for e in run if e.gen == gen and acc.first <= e.collections <= acc.last}
            charged = charges(ingested, (iid, gen))

            assert truth
            assert set(charged) == truth, f"gen {gen} charges {set(charged) ^ truth} it should not"
            assert [c for c in truth if charged[c] != 1] == [], f"gen {gen} charges a collection twice"

    @pytest.mark.parametrize(("gap_ns", "per_tick"), PACES)
    def test_the_partition_has_loss_in_it_to_get_wrong(self, gap_ns: int, per_tick: int) -> None:
        """Otherwise the check above would pass on a run that lost nothing,
        where the observed records partition the span on their own and no
        range is exercised at all."""
        run = build_interleaved_run(2_000, gap_ns=gap_ns)

        ingested = observe_all(interpreter_polls(run, per_tick))

        assert any(ingested.windows_for(key) for key in ingested.cursors)
        for key, acc in ingested.cursors.items():
            assert sum(w.lost_count for w in ingested.windows_for(key)) == acc.lost_count

    @pytest.mark.parametrize(("gap_ns", "per_tick"), PACES)
    def test_each_range_abuts_the_records_that_bound_it(self, gap_ns: int, per_tick: int) -> None:
        """The fences, stated directly rather than as a consequence. A window
        opens one counter past the last record gcmon saw before it and closes
        one short of the first it saw after, and both of those are drawn."""
        run = build_interleaved_run(2_000, gap_ns=gap_ns)

        ingested = observe_all(interpreter_polls(run, per_tick))

        for key, windows in ingested.windows.items():
            seen = set(ingested.observed_for(key))
            for w in windows:
                assert w.lost_from - 1 in seen
                assert lost_to(w.lost_from, w.lost_count) + 1 in seen

    def test_the_capture_names_the_records_it_lost(self) -> None:
        """The same partition on the verbatim two-poll capture, where the
        counters are real. gen 0 ran 466 through 563 and gcmon saw 22 of
        them."""
        captured = observe_all([build_batch(POLL_0), build_batch(POLL_1)])

        acc = captured[(0, 0)]
        gap = captured.windows_for((0, 0))[0]

        assert (gap.lost_from, lost_to(gap.lost_from, gap.lost_count)) == (477, 552)
        assert charges(captured, (0, 0)) == Counter(range(acc.first, acc.last + 1))


class TestCaptureFixture:
    """Gap counts against the verbatim two-poll capture in test_monitor_cursor.

    The capture recorded no ``duration``, so every record carries the factory
    default and only counts mean anything here. The pause arithmetic is
    covered by the synthetic runs above.
    """

    def test_gen_0_lost_seventy_six_records(self, captured: Ingested) -> None:
        acc = captured[(0, 0)]

        assert (acc.first, acc.last) == (466, 563)
        assert [w.lost_count for w in captured.windows_for((0, 0))] == [76]
        assert acc.sampled_count == 22

    def test_gen_1_lost_five_records(self, captured: Ingested) -> None:
        acc = captured[(0, 1)]

        assert (acc.first, acc.last) == (41, 51)
        assert [w.lost_count for w in captured.windows_for((0, 1))] == [5]

    def test_an_unchanged_generation_loses_nothing(self, captured: Ingested) -> None:
        acc = captured[(0, 2)]

        assert acc.sampled_count == 1
        assert captured.windows_for((0, 2)) == []

    def test_the_window_spans_the_unobserved_interval(self, captured: Ingested) -> None:
        """From the newest record in the first poll to gen 0's own oldest in
        the second: 90 ms of a 100 ms tick. Both bounds come from time order,
        not slot order, and both are what the two polls prove about gen 0."""
        gap = captured.windows_for((0, 0))[0]

        assert gap.ts_start == 294787154918900  # gen 0 collections=476, newest in POLL_0
        assert gap.ts_stop == 294787244879600  # gen 0 collections=488, oldest in POLL_1
        assert gap.ts_stop - gap.ts_start == pytest.approx(90_000_000, rel=0.01)

    def test_both_generations_start_where_the_poll_confirmed(self, captured: Ingested) -> None:
        """One bulk read gives every key in an interpreter the same
        confirmation point, so two keys losing records across one poll are
        blind from the same instant."""
        assert captured.windows_for((0, 0))[0].ts_start == captured.windows_for((0, 1))[0].ts_start

    def test_each_span_is_drawn_at_the_full_width_of_its_own_window(self, captured: Ingested) -> None:
        """POLL_1 recovered two gen-1 records inside gen 0's window, and gen
        0's span is drawn over them rather than cut around them. Where the
        missing records ran is what the span leaves open; those two collections
        are drawn on the interpreter's own row, so a reader can see them."""
        gen0, gen1 = captured.windows_for((0, 0))[0], captured.windows_for((0, 1))[0]
        observed = [(e.ts_start, e.ts_stop) for e in build_batch(POLL_1) if e.ts_start < e.ts_stop]

        spans = captured.spans()

        assert [(s.ts_start, s.ts_stop) for s in spans] == [
            (gen0.ts_start, gen0.ts_stop),
            (gen1.ts_start, gen1.ts_stop),
        ]
        assert any(spans[0].ts_start < start < spans[0].ts_stop for start, _stop in observed)

    def test_each_generation_gets_a_span_carrying_its_own_loss(self, captured: Ingested) -> None:
        """Two bars rather than one, and the row now says which generation
        went blind and for how long."""
        spans = captured.spans()

        assert [(s.gen, s.lost_count) for s in spans] == [(0, 76), (1, 5)]

    def test_the_two_generations_nest_widest_first(self, captured: Ingested) -> None:
        """Real data producing the shape the ordering is for: both windows sit
        inside one tick and open at the same instant, so gen 0's wider span has
        to go out first or gen 1's END closes it."""
        spans = captured.spans()

        assert spans[0].ts_start == spans[1].ts_start
        assert depths(spans) == [0, 1]


class TestOneLeftEdgePerPoll:
    """Every window a poll opens for one interpreter starts at the same
    instant.

    A bulk read covers all of an interpreter's generations, so they share one
    confirmation point and a window can only open at it. Nothing else may
    raise the bound for one ring alone: two windows opening at different
    instants would cross rather than nest, and the loss track is a stack.
    """

    def test_two_generations_losing_across_one_poll_share_the_edge(self) -> None:
        gen0 = build_run(9, gen=0)
        gen1 = build_run(9, gen=1, spacing_ns=SPACING_NS // 2, ts0=TS0 + 300_000)

        ingested = Ingested()
        ingested.poll([gen0[0], gen1[0]])
        opened = ingested.poll([gen0[5], gen1[6]])

        assert len(opened[0]) == 2
        assert len({w.ts_start for w in opened[0]}) == 1

    @pytest.mark.parametrize(("gap_ns", "per_tick"), PACES)
    def test_it_holds_over_a_whole_interpreter(self, gap_ns: int, per_tick: int) -> None:
        run = build_interleaved_run(2_000, gap_ns=gap_ns)

        ingested = Ingested()
        polls = [ingested.poll(batch) for batch in interpreter_polls(run, per_tick)]

        assert any(len(opened.get(0, [])) > 1 for opened in polls)
        for opened in polls:
            for windows in opened.values():
                assert len({w.ts_start for w in windows}) <= 1

    def test_a_mid_write_slot_does_not_move_one_ring_edge(self) -> None:
        """A record caught part-written is dropped and comes back a poll
        later; the cursor never sees it, so it neither opens a window nor
        bounds one. Its generation's window opens where every other one does.
        """
        gen0 = build_run(4, gen=0, spacing_ns=44_000_000)
        gen1 = build_run(3, gen=1, spacing_ns=44_000_000, ts0=TS0 + 10_000_000)
        # The slot as the poll caught it: `ts_start` published, `ts_stop` still
        # carrying the value memcpy'd from the record before it.
        mid_write = msgspec.structs.replace(gen1[0], ts_stop=gen1[0].ts_start - 1_000)

        ingested = Ingested()
        ingested.poll([gen0[0], mid_write])
        opened = ingested.poll([gen1[0], gen0[2], gen0[3]])

        assert ingested.windows_for((0, 1)) == []
        assert [w.ts_start for w in opened[0]] == [gen0[0].ts_stop]


class TestStackOrder:
    """The one job is the order, and getting it wrong is silent.

    Nothing here reshapes a window: a poll's windows for one interpreter share
    a left edge, so they nest already. What the sort settles is which of two
    spans opening at the same instant goes out first, and a track is a stack.
    """

    def test_nothing_orders_to_nothing(self) -> None:
        assert stack_order([]) == []

    def test_a_shared_left_edge_goes_widest_first(self) -> None:
        """Three generations blind across one poll. Narrowest first would have
        gen 2's END close gen 0's span, and the trace would say nothing."""
        ordered = stack_order(
            [
                window(gen=0, ts_start=10, ts_stop=20),
                window(gen=1, ts_start=10, ts_stop=30),
                window(gen=2, ts_start=10, ts_stop=40),
            ]
        )

        assert [w.gen for w in ordered] == [2, 1, 0]
        assert depths(ordered) == [0, 1, 2]

    def test_input_order_does_not_matter(self) -> None:
        """`_ingest` walks its keys in `groupby` order, which is generation
        order, which has nothing to do with width."""
        windows = [
            window(gen=1, ts_start=10, ts_stop=30),
            window(gen=2, ts_start=10, ts_stop=40),
            window(gen=0, ts_start=10, ts_stop=20),
        ]

        assert stack_order(windows) == stack_order(list(reversed(windows)))

    def test_disjoint_windows_come_back_in_time_order(self) -> None:
        ordered = stack_order([window(ts_start=50, ts_stop=60), window(ts_start=10, ts_stop=20)])

        assert [w.ts_start for w in ordered] == [10, 50]

    def test_a_nested_window_follows_the_one_around_it(self) -> None:
        ordered = stack_order([window(gen=1, ts_start=10, ts_stop=20), window(gen=0, ts_start=10, ts_stop=100)])

        assert [(w.ts_start, w.ts_stop) for w in ordered] == [(10, 100), (10, 20)]

    def test_touching_windows_stay_two_spans(self) -> None:
        """One poll's, then the next poll's. Each says what its own counters
        say, and neither reaches into the other."""
        ordered = stack_order([window(ts_start=10, ts_stop=20), window(gen=1, ts_start=20, ts_stop=30)])

        assert [(w.ts_start, w.ts_stop) for w in ordered] == [(10, 20), (20, 30)]
        assert depths(ordered) == [0, 0]


class TestStackOrderProperties:
    """Properties the loss track depends on, over a spread of shapes."""

    @pytest.mark.parametrize("shape", SHAPES)
    def test_the_order_is_one_a_stack_can_take(self, shape: list[tuple[int, int, int]]) -> None:
        ordered = stack_order([window(gen=g, ts_start=a, ts_stop=b) for g, a, b in shape])

        depths(ordered)

    @pytest.mark.parametrize("shape", SHAPES)
    def test_every_window_is_drawn_exactly_once(self, shape: list[tuple[int, int, int]]) -> None:
        """Ordering is all that happens: nothing merges, splits or drops."""
        windows = [window(gen=g, ts_start=a, ts_stop=b, lost_count=g + 1) for g, a, b in shape]

        ordered = stack_order(windows)

        assert sorted(ordered, key=id) == sorted(windows, key=id)
        assert sum(w.lost_count for w in ordered) == sum(w.lost_count for w in windows)


class TestToLossMsg:
    def test_carries_the_window_and_the_generation_it_belongs_to(self) -> None:
        msg = to_loss_msg(1, window(gen=1, ts_start=10, ts_stop=99, lost_count=5, lost_pause_ns=7))

        assert (msg.iid, msg.gen, msg.ts_start, msg.ts_stop) == (1, 1, 10, 99)
        assert (msg.lost_count, msg.lost_pause_ns) == (5, 7)

    def test_the_range_survives_the_handover_to_the_exporters(self) -> None:
        """The record is the only thing past this point: a window dropped here
        would leave every span in the trace back to counting alone."""
        msg = to_loss_msg(0, window(lost_from=413, lost_count=19))

        assert msg.lost_from == 413
        assert lost_to(msg.lost_from, msg.lost_count) == 431

    def test_a_capture_yields_one_record_per_generation(self, captured: Ingested) -> None:
        msgs = [to_loss_msg(0, w) for w in captured.spans()]

        assert [(m.gen, m.lost_count) for m in msgs] == [(0, 76), (1, 5)]


class TestConfirmedByInterpreter:
    def test_no_cursors_at_all(self) -> None:
        assert confirmed_by_interpreter({}) == {}

    def test_takes_the_latest_record_across_generations(self) -> None:
        """One bulk read covers all three, so the newest record any of them
        returned bounds when that read happened."""
        cursors = {
            (0, 0): KeyAccumulator(last_ts_stop=500),
            (0, 1): KeyAccumulator(last_ts_stop=120),
            (0, 2): KeyAccumulator(last_ts_stop=40),
        }

        assert confirmed_by_interpreter(cursors) == {0: 500}

    def test_interpreters_stay_apart(self) -> None:
        """Separate reads, separate confirmation points."""
        cursors = {(0, 0): KeyAccumulator(last_ts_stop=500), (1, 0): KeyAccumulator(last_ts_stop=90)}

        assert confirmed_by_interpreter(cursors) == {0: 500, 1: 90}

    def test_an_unobserved_key_confirms_nothing(self) -> None:
        assert confirmed_by_interpreter({(0, 0): KeyAccumulator()}) == {0: 0}


class TestQuietGeneration:
    """A poll finding a counter unchanged is evidence, not silence: it proves
    nothing was lost on that key up to that read. Without it a generation that
    goes quiet and later loses records would open a window reaching back over
    every tick it sat out, ticks in which the polls proved it lost nothing."""

    def polls(self) -> Ingested:
        gen0 = build_run(9, gen=0)
        # Collects once at the start, then not again until well after the
        # second poll, by which point three of its records are already gone.
        gen2 = build_run(7, gen=2, spacing_ns=3 * SPACING_NS)

        ingested = Ingested()
        ingested.poll([*gen0[0:3], gen2[0]])
        ingested.poll([*gen0[3:6], gen2[0]])
        ingested.poll([*gen0[6:9], *gen2[4:7]])
        return ingested

    def test_the_window_starts_where_the_last_poll_confirmed(self) -> None:
        gen0 = build_run(9, gen=0)
        ingested = self.polls()

        gap = ingested.windows_for((0, 2))[0]

        assert gap.lost_count == 3
        assert gap.ts_start == gen0[5].ts_stop  # newest record in the confirming poll
        assert gap.ts_stop == build_run(7, gen=2, spacing_ns=3 * SPACING_NS)[4].ts_start

    def test_it_does_not_reach_back_to_the_generation_own_last_record(self) -> None:
        gen2 = build_run(7, gen=2, spacing_ns=3 * SPACING_NS)
        ingested = self.polls()

        gap = ingested.windows_for((0, 2))[0]

        assert gap.ts_start > gen2[0].ts_stop

    def test_an_unchanged_counter_opens_nothing(self) -> None:
        ingested = self.polls()

        assert len(ingested.windows_for((0, 2))) == 1


class TestASpanCoveringAnObservedCollection:
    """A window bracketing a collection that *was* observed draws whole, as
    one bar over the top of it.

    Traced from a real capture: gen 0 collected every ~45 ms, records were
    lost, and an observed gen-1 collection sat 66 ms into the gap. No lost
    record ran during it, since collections in an interpreter are serialized,
    so the span is wider than the stretch the records can be in. That is
    accepted rather than corrected: the span bounds *where* they are, and the
    gen-1 collection is drawn on the interpreter's own row above, so a reader
    narrows it from evidence already on screen. Cutting the bar did the
    narrowing for them, and charged the remaining pieces counts and pause
    neither had room for.
    """

    GEN0 = 5
    SPACING_NS = 44_000_000
    GEN1_TS = TS0 + 66_000_000

    def gen0(self) -> list[GCStatsInfo]:
        return build_run(self.GEN0, gen=0, spacing_ns=self.SPACING_NS)

    def gen1(self) -> GCStatsInfo:
        return build_run(1, gen=1, ts0=self.GEN1_TS)[0]

    def polls(self) -> Ingested:
        """Two gen-0 records lost across an observed gen-1 collection."""
        gen0 = self.gen0()

        ingested = Ingested()
        ingested.poll([gen0[0]])
        ingested.poll([self.gen1(), gen0[3], gen0[4]])
        return ingested

    def test_the_window_reaches_its_own_next_record(self) -> None:
        gap = self.polls().windows_for((0, 0))[0]

        assert gap.lost_count == 2
        assert gap.ts_stop == self.gen0()[3].ts_start

    def test_it_draws_as_one_span_from_end_to_end(self) -> None:
        gen0 = self.gen0()

        spans = self.polls().spans()

        assert [(s.ts_start, s.ts_stop) for s in spans] == [(gen0[0].ts_stop, gen0[3].ts_start)]

    def test_the_span_covers_the_observed_collection(self) -> None:
        """The consequence being accepted, pinned so it cannot regress into a
        cut by accident."""
        gen1 = self.gen1()

        span = self.polls().spans()[0]

        assert span.ts_start < gen1.ts_start and gen1.ts_stop < span.ts_stop

    def test_the_span_holds_the_pause_it_reports(self) -> None:
        """What the cut broke. Two collections and their pause were shared
        over the leftover pieces by width, and a piece could end up narrower
        than the pause handed to it."""
        span = self.polls().spans()[0]

        assert span.lost_pause_ns <= span.ts_stop - span.ts_start

    def test_the_spans_are_disjoint_and_in_order(self) -> None:
        """They share the loss track, so they have to stay a stack."""
        spans = self.polls().spans()

        assert all(a.ts_stop <= b.ts_start for a, b in pairwise(spans))

    def test_the_loss_rides_whole_on_the_one_span(self) -> None:
        """Nothing says which side of the observed collection each lost record
        fell, and the span no longer guesses: it carries both."""
        spans = self.polls().spans()

        assert [s.lost_count for s in spans] == [2]

    def test_a_window_holding_one_record_is_drawn_whole_too(self) -> None:
        """The cut left this one as a single piece and dropped the other,
        having no record to give it. One bar either way, but this one spans
        the whole blind interval rather than the part left over."""
        gen0 = self.gen0()

        ingested = Ingested()
        ingested.poll([gen0[0]])
        ingested.poll([self.gen1(), gen0[2], gen0[3]])

        spans = ingested.spans()
        assert [(s.ts_start, s.ts_stop) for s in spans] == [(gen0[0].ts_stop, gen0[2].ts_start)]
        assert spans[0].lost_count == 1
        assert spans[0].lost_pause_ns > 0

    def test_a_window_with_nothing_inside_it_is_unchanged(self) -> None:
        gen0 = build_run(4, gen=0, spacing_ns=44_000_000)

        ingested = Ingested()
        ingested.poll([gen0[0]])
        ingested.poll([gen0[2], gen0[3]])

        assert [(s.ts_start, s.ts_stop) for s in ingested.spans()] == [(gen0[0].ts_stop, gen0[2].ts_start)]

    def test_drawing_leaves_the_arithmetic_alone(self) -> None:
        """The window still reports what the counters say, and it is the
        window `StreamingStats` was given, before anything was drawn."""
        gap = self.polls().windows_for((0, 0))[0]

        assert gap.lost_count == 2


class TestAWindowAnObservationCoversEndToEnd:
    """An observed collection can span a window from end to end.

    A key's own neighbours bound its windows and collections are serialized,
    so truthful data cannot reach this: a gen-1 collection filling the whole
    interval leaves the lost gen-0 records nowhere to have run. The cut took
    it literally and drew nothing at all, which put the loss on no bar in the
    trace while `--stats` went on counting it. Drawn whole, the span says what
    the counters say and the reader can see the contradiction.
    """

    def polls(self) -> tuple[Ingested, list[GCStatsInfo], GCStatsInfo]:
        gen0 = build_run(4, gen=0, spacing_ns=44_000_000)
        # Exactly the window: it opens at gen0[0]'s stop and runs to gen0[2]'s
        # start, which is where gen 0's own next record ends the blind stretch.
        covering = msgspec.structs.replace(build_run(1, gen=1)[0], ts_start=gen0[0].ts_stop, ts_stop=gen0[2].ts_start)

        ingested = Ingested()
        ingested.poll([gen0[0]])
        ingested.poll([covering, gen0[2], gen0[3]])
        return ingested, gen0, covering

    def test_the_span_is_drawn(self) -> None:
        ingested, gen0, covering = self.polls()

        spans = ingested.spans()

        assert [(s.ts_start, s.ts_stop) for s in spans] == [(covering.ts_start, covering.ts_stop)]
        assert (spans[0].ts_start, spans[0].ts_stop) == (gen0[0].ts_stop, gen0[2].ts_start)

    def test_it_carries_the_loss_the_counters_reported(self) -> None:
        ingested, _gen0, _covering = self.polls()

        assert ingested.spans()[0].lost_count == 1


class TestCounterOrderNotClockOrder:
    """``observe_batch`` folds a run by counter: the run's first record is the
    only one that can sit across a gap, and its last one settles the cursor.

    ``_ingest`` sorts on ``collections`` to give it that. A healthy ring makes
    the two orders agree, so this is the invariant holding rather than a bug
    reproducing. The cursor still means a counter, and a batch where the clock
    disagrees must not walk it backwards.
    """

    def skewed(self, events: Sequence[GCStatsInfo], nth: int) -> GCStatsInfo:
        """*nth* with the earliest ``ts_start`` in the run, counter intact."""
        return msgspec.structs.replace(events[nth], ts_start=events[0].ts_start - 1)

    def test_the_cursor_lands_on_the_highest_counter(self) -> None:
        events = build_run(3)

        ingested = Ingested()
        ingested.poll([events[0], events[1], self.skewed(events, 2)])

        assert ingested[(0, 0)].last == 3

    def test_the_next_poll_finds_no_phantom_gap(self) -> None:
        """A cursor left short reports the records past it as lost, and
        re-emits them once they are read again."""
        events = build_run(5)

        ingested = Ingested()
        ingested.poll([events[0], events[1], self.skewed(events, 2)])
        ingested.poll([events[3]])

        assert ingested.windows_for((0, 0)) == []


class TestIsDrawable:
    """The predicate alone, on hand-built bounds.

    Everything below it goes through a poll; this pins what the poll's result
    is being tested against.
    """

    def test_an_interval_with_room_in_it_is_drawable(self) -> None:
        assert window(ts_start=10, ts_stop=20).is_drawable

    def test_one_nanosecond_is_room_enough(self) -> None:
        assert window(ts_start=10, ts_stop=11).is_drawable

    def test_bounds_running_backwards_are_not(self) -> None:
        assert not window(ts_start=20, ts_stop=10).is_drawable

    def test_equal_bounds_are_not(self) -> None:
        """Zero width is not a narrow interval, it is no interval: the lost
        records had nowhere to run, and the slice would draw sub-pixel."""
        assert not window(ts_start=10, ts_stop=10).is_drawable

    def test_it_reads_the_bounds_and_nothing_else(self) -> None:
        """The counts come from the ring's own counters and stay true across
        bounds that are not, so they cannot be allowed to vote here."""
        assert not window(ts_start=20, ts_stop=10, lost_count=5, lost_pause_ns=99).is_drawable


# gen 1's single record, moved about while its pause length stays put. Where
# it lands decides `confirmed` for the whole interpreter, and so where the
# gen-0 window opens; it changes nothing gcmon counts.
GEN1_PAUSE_NS = varied_pause(1)
GEN0_SPACING_NS = 1_000_000
GEN0_FOURTH_TS = TS0 + 3 * GEN0_SPACING_NS  # `gen0[3].ts_start`, the window's far end


def inverting_polls(gen1_ts0: int) -> Ingested:
    """Two polls losing two gen-0 records, with gen 1 parked at *gen1_ts0*.

    `confirmed_by_interpreter` takes the maximum ``last_ts_stop`` across the
    interpreter's rings, so parking gen 1 past the gen-0 record that closes
    the window opens that window after it ends. Nothing gcmon reads is
    inconsistent taken one record at a time; the pair is.
    """
    gen0 = build_run(5, gen=0, spacing_ns=GEN0_SPACING_NS)
    gen1 = build_run(1, gen=1, ts0=gen1_ts0)[0]

    ingested = Ingested()
    ingested.poll([gen0[0], gen1])
    ingested.poll([gen0[3], gen0[4]])
    return ingested


# Three placements of that one gen-1 record: behind everything, so the window
# comes out ordinary; exactly on the window's far end, so it comes out
# zero-width; and past it, so it comes out backwards.
DRAWABLE_GEN1_TS = TS0 - 5_000_000
TOUCHING_GEN1_TS = GEN0_FOURTH_TS - GEN1_PAUSE_NS
INVERTED_GEN1_TS = TS0 + 10_000_000


class TestAWindowWithNoRoomInIt:
    """A poll can hand back a window whose bounds describe no interval.

    ``confirmed`` is one maximum across the interpreter's rings, so a fresh
    record starting behind a record another ring already returned opens a
    window that closes before it opens. Truthful data cannot produce that:
    collections in an interpreter are serialized and ``add_stats`` publishes
    ``ts_stop`` last so a remote reader never selects a half-written record.
    ADR-0015 §"What gcmon trusts the target for" leaves the fix upstream and
    records that gcmon detects none of it — this window is the one
    client-side sign of it there is, and one gcmon trips over rather than
    looks for.

    What the poll does with it: count the loss, draw nothing. The record
    counters carry no timestamp, so the loss survives bounds that do not.
    """

    def test_a_backwards_window_is_measured(self) -> None:
        gap = inverting_polls(INVERTED_GEN1_TS).windows_for((0, 0))[0]

        assert gap.ts_stop < gap.ts_start
        assert (gap.lost_from, gap.lost_count) == (2, 2)

    def test_it_is_not_drawn(self) -> None:
        assert inverting_polls(INVERTED_GEN1_TS).spans() == []

    def test_it_is_counted(self) -> None:
        assert inverting_polls(INVERTED_GEN1_TS).undrawable_count() == 1

    def test_a_window_of_no_width_goes_the_same_way(self) -> None:
        """Equal bounds draw as an invisible sliver rather than a backwards
        slice, which is worse: the row looks whole."""
        ingested = inverting_polls(TOUCHING_GEN1_TS)
        gap = ingested.windows_for((0, 0))[0]

        assert gap.ts_start == gap.ts_stop == GEN0_FOURTH_TS
        assert ingested.spans() == []
        assert ingested.undrawable_count() == 1

    def test_the_same_polls_draw_the_window_when_it_has_room(self) -> None:
        """The control. Only the gen-1 record moves, and it is what decides
        whether the gen-0 window has an interval in it."""
        ingested = inverting_polls(DRAWABLE_GEN1_TS)

        assert [(s.ts_start, s.ts_stop) for s in ingested.spans()] == [(TS0 + GEN1_PAUSE_NS, GEN0_FOURTH_TS)]
        assert ingested.undrawable_count() == 0

    def test_the_loss_is_the_same_either_way(self) -> None:
        """What the accumulator holds cannot depend on whether a span was
        drawn: `Cov`, `F` and the exact totals all come off these."""
        inverted = inverting_polls(INVERTED_GEN1_TS)[(0, 0)]
        drawable = inverting_polls(DRAWABLE_GEN1_TS)[(0, 0)]

        assert inverted.exact_count == drawable.exact_count == 5
        assert inverted.lost_count == drawable.lost_count == 2
        assert inverted.exact_pause_ns == drawable.exact_pause_ns
        assert inverted.coverage == drawable.coverage
        assert inverted.scale_factor == drawable.scale_factor


class TestOneSpanPerMeasuredWindow:
    """Every window a poll measured reaches the loss track as exactly one
    span, and the only shortfall permitted is the undrawable one.

    The count has to close the gap exactly, or a window could go missing with
    nothing anywhere saying so — which is the failure a silent discard would
    introduce in place of the one it prevents.
    """

    @pytest.mark.parametrize(("gap_ns", "per_tick"), PACES)
    def test_a_run_of_sound_records_draws_all_of_them(self, gap_ns: int, per_tick: int) -> None:
        ingested = observe_all(interpreter_polls(build_interleaved_run(2_000, gap_ns=gap_ns), per_tick))

        assert ingested.measured()
        assert ingested.undrawable_count() == 0
        assert len(ingested.spans()) == len(ingested.measured())

    @pytest.mark.parametrize("gen1_ts0", [DRAWABLE_GEN1_TS, TOUCHING_GEN1_TS, INVERTED_GEN1_TS])
    def test_the_count_closes_the_gap_exactly(self, gen1_ts0: int) -> None:
        ingested = inverting_polls(gen1_ts0)

        assert all(span.is_drawable for span in ingested.spans())
        assert len(ingested.spans()) + ingested.undrawable_count() == len(ingested.measured())


PID = 12345


class LossRecorder(EventsExporter):
    """Every loss record `_ingest` handed an exporter, in emission order."""

    def __init__(self) -> None:
        super().__init__()
        self.losses: list[TLossMsg] = []

    @override
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        pass

    @override
    def add_loss_event(self, pid: int, item: TLossMsg) -> None:
        self.losses.append(item)

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        pass

    @override
    def close(self) -> None:
        pass


def ingest_for_real(gen1_ts0: int) -> tuple[LossRecorder, StreamingStats]:
    """`inverting_polls` again, through `EventsMonitor._ingest` itself."""
    gen0 = build_run(5, gen=0, spacing_ns=GEN0_SPACING_NS)
    gen1 = build_run(1, gen=1, ts0=gen1_ts0)[0]

    recorder = LossRecorder()
    stats = StreamingStats()
    monitor = EventsMonitor(ExternalProcess(pid=PID), recorder, stats)
    monitor._ingest(PID, [gen0[0], gen1])
    monitor._ingest(PID, [gen0[3], gen0[4]])
    return recorder, stats


class TestTheMonitorHoldsBackTheWindowItself:
    """The same two polls down the real path, since `Ingested` only mirrors
    it and the split between recording and drawing is what is under test.

    `_ingest` has to record the loss and then decline to draw it, in that
    order. Rejecting in `_open_run` would take `record_loss` with it and lose
    the collections from every total; filtering in `stack_order` would hide
    the rejection where nothing counts it.
    """

    def test_the_backwards_window_reaches_no_exporter(self) -> None:
        recorder, _stats = ingest_for_real(INVERTED_GEN1_TS)

        assert recorder.losses == []

    def test_the_monitor_counts_it(self) -> None:
        _recorder, stats = ingest_for_real(INVERTED_GEN1_TS)

        assert stats.undrawable_count(PID, 0) == 1

    def test_the_collections_are_recorded_all_the_same(self) -> None:
        _recorder, stats = ingest_for_real(INVERTED_GEN1_TS)

        assert stats.lost_count(PID, 0) == 2
        assert stats.lost_pause_ns(PID, 0) > 0

    def test_the_same_polls_export_a_span_when_the_window_has_room(self) -> None:
        recorder, stats = ingest_for_real(DRAWABLE_GEN1_TS)

        assert [(m.gen, m.lost_count) for m in recorder.losses] == [(0, 2)]
        assert stats.undrawable_count(PID, 0) == 0

    def test_nothing_the_table_shows_moves(self) -> None:
        """Every cell of the `--stats` row comes from counters, so holding a
        span back cannot shift one. Checked rather than assumed: it is the
        claim that lets the discard be silent outside the footer."""
        _held, held_stats = ingest_for_real(INVERTED_GEN1_TS)
        _drawn, drawn_stats = ingest_for_real(DRAWABLE_GEN1_TS)

        assert held_stats.exact_count(PID, 0) == drawn_stats.exact_count(PID, 0) == 5
        assert held_stats.lost_count(PID, 0) == drawn_stats.lost_count(PID, 0)
        assert held_stats.lost_pause_ns(PID, 0) == drawn_stats.lost_pause_ns(PID, 0)
        assert held_stats.exact_pause_ns(PID, 0) == drawn_stats.exact_pause_ns(PID, 0)
        assert held_stats.coverage(PID, 0) == drawn_stats.coverage(PID, 0)
        assert held_stats.scale_factor(PID, 0) == drawn_stats.scale_factor(PID, 0)

    @pytest.mark.parametrize("gen1_ts0", [DRAWABLE_GEN1_TS, TOUCHING_GEN1_TS, INVERTED_GEN1_TS])
    def test_the_mirror_still_mirrors(self, gen1_ts0: int) -> None:
        """`Ingested` stands in for `_ingest` throughout this file, so where
        both can be run the two have to agree."""
        recorder, stats = ingest_for_real(gen1_ts0)
        mirrored = inverting_polls(gen1_ts0)

        assert [(m.gen, m.ts_start, m.ts_stop) for m in recorder.losses] == [
            (s.gen, s.ts_start, s.ts_stop) for s in mirrored.spans()
        ]
        assert stats.undrawable_count(PID, 0) == mirrored.undrawable_count()
        assert stats.lost_count(PID, 0) == sum(w.lost_count for w in mirrored.measured())

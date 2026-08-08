"""Tests for reconstructing what a poll could not observe.

Two kinds of input here. The synthetic runs below carry a cumulative
``duration`` the way a real target does, so they can check the arithmetic
against ground truth: build a full run, show the accumulator only what
survives a ring of a given size, and compare what it reconstructs to what
actually happened. The capture fixture from ``test_monitor_cursor`` carries
no durations, so it checks gap counts against real slot data instead.
"""

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from itertools import groupby, pairwise

import msgspec.structs
import pytest

from gcmon.data import GCStatsInfo, secs_to_ns
from gcmon.loss import (
    KeyAccumulator,
    LossWindow,
    MergedLoss,
    confirmed_by_interpreter,
    merge_windows,
    to_loss_msg,
)
from tests.helpers import create_mock_stats_item
from tests.test_monitor_cursor import POLL_0, POLL_1, build_batch

TS0 = 1_000_000_000
SPACING_NS = 1_150_000  # measured gap between gen-0 collections

# (gen, ts_start, ts_stop) triples: single, crossing, touching, nested,
# disjoint, zero-length, and a run mixing several of those.
SHAPES: list[list[tuple[int, int, int]]] = [
    [],
    [(0, 10, 20)],
    [(0, 10, 20), (1, 15, 25), (2, 24, 40)],
    [(0, 10, 20), (1, 20, 30), (2, 30, 40)],
    [(0, 0, 100), (1, 10, 20), (2, 30, 40)],
    [(0, 10, 20), (1, 100, 110), (2, 200, 210)],
    [(0, 5, 5), (1, 5, 5)],
    [(0, 1, 20), (1, 2, 25), (0, 30, 45), (1, 44, 50), (2, 80, 90)],
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

    Mirrors ``EventsMonitor._ingest``, which merges and emits each poll's
    windows rather than retaining them, so a test that wants to look at a
    window has to collect it on the way past.
    """

    def __init__(self) -> None:
        self.cursors: dict[tuple[int, int], KeyAccumulator] = {}
        self.windows: dict[tuple[int, int], list[LossWindow]] = {}
        self.in_flight: dict[int, int] = {}

    def poll(self, batch: Sequence[GCStatsInfo]) -> dict[int, list[LossWindow]]:
        """Fold one whole ring buffer; return the windows it opened, by iid.

        Slot order is not time order, so walking the batch as it came would
        seed ``first`` from whichever record sat at the ring's write position.
        Sort each ring back into counter order, drop what the cursor has
        already passed, and hand the rest over as one run.
        """
        confirmed = confirmed_by_interpreter(self.cursors)
        for iid, since in self.in_flight.items():
            confirmed[iid] = max(confirmed.get(iid, 0), since)
            finished = [e.ts_stop for e in batch if e.iid == iid and e.ts_start < e.ts_stop and e.ts_start <= since]
            if finished:
                confirmed[iid] = max(confirmed[iid], max(finished))
        self.in_flight = {
            e.iid: max(self.in_flight.get(e.iid, 0), e.ts_start) for e in batch if e.ts_start >= e.ts_stop
        }

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
                opened.setdefault(key[0], []).append(window)
                self.windows.setdefault(key, []).append(window)

        return opened

    def spans(self, iid: int = 0) -> list[MergedLoss]:
        """What `_ingest` would draw: this interpreter's windows, merged.

        Each span is drawn at the full width of the windows inside it, so
        every number on it is the one the target's counters gave.
        """
        windows = [w for key, ws in self.windows.items() if key[0] == iid for w in ws]
        return merge_windows(windows)

    def __getitem__(self, key: tuple[int, int]) -> KeyAccumulator:
        return self.cursors[key]

    def windows_for(self, key: tuple[int, int]) -> list[LossWindow]:
        return self.windows.get(key, [])


def observe_all(batches: Iterable[Sequence[GCStatsInfo]]) -> Ingested:
    ingested = Ingested()
    for batch in batches:
        ingested.poll(batch)
    return ingested


def true_pause_ns(events: Sequence[GCStatsInfo], first: int, last: int) -> int:
    """Ground truth: the pause sum over collections *first* through *last*."""
    return sum(e.ts_stop - e.ts_start for e in events if first <= e.collections <= last)


def window(
    gen: int = 0, ts_start: int = 0, ts_stop: int = 0, lost_count: int = 1, lost_pause_ns: int = 0
) -> LossWindow:
    return LossWindow(ts_start=ts_start, ts_stop=ts_stop, gen=gen, lost_count=lost_count, lost_pause_ns=lost_pause_ns)


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
        """Merging is a drawing decision, so it must not change the totals."""
        ingested = observe_all(ring_polls(build_run(400), capacity, per_tick))

        spans = ingested.spans()
        assert sum(s.lost_count.get(0, 0) for s in spans) == ingested[(0, 0)].lost_count
        assert sum(s.lost_pause_ns.get(0, 0) for s in spans) == sum(
            w.lost_pause_ns for w in ingested.windows_for((0, 0))
        )


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

    It holds now because every number on a span comes from the target's
    counters over the span's own bounds. A window's pause is what the target
    collected between two of that key's own records; merged windows lie inside
    the union they merge into; and collections in an interpreter are
    serialized, so nothing is charged twice and all of it fits.
    """

    @pytest.mark.parametrize(("gap_ns", "per_tick"), PACES)
    def test_every_span_has_room_for_the_pause_it_reports(self, gap_ns: int, per_tick: int) -> None:
        run = build_interleaved_run(2_000, gap_ns=gap_ns)

        spans = observe_all(interpreter_polls(run, per_tick)).spans()

        assert spans
        for span in spans:
            assert sum(span.lost_pause_ns.values()) <= span.ts_stop - span.ts_start

    @pytest.mark.parametrize(("gap_ns", "per_tick"), PACES)
    def test_no_span_is_drawn_reporting_nothing(self, gap_ns: int, per_tick: int) -> None:
        """Every bar on the track stands for records the counters say went
        missing. A span carrying none claims a stretch was blind while saying
        gcmon has no evidence anything happened in it."""
        run = build_interleaved_run(2_000, gap_ns=gap_ns)

        spans = observe_all(interpreter_polls(run, per_tick)).spans()

        assert all(sum(span.lost_count.values()) > 0 for span in spans)

    def test_the_run_puts_two_generations_on_one_span(self) -> None:
        """Otherwise the check above would only ever see single-generation
        spans, whose bound each window already carries on its own, and the
        merge would go untested where it matters."""
        spans = observe_all(interpreter_polls(build_interleaved_run(2_000), 87)).spans()

        assert any(len(span.lost_count) > 1 for span in spans)

    def test_a_span_reaches_over_a_collection_gcmon_observed(self) -> None:
        """The consequence accepted in exchange. The span bounds where the
        missing records are; the observed collection is drawn on the
        interpreter's own row above, so a reader narrows it from there."""
        batches = list(interpreter_polls(build_interleaved_run(2_000), 87))
        observed = {(e.gen, e.collections): e for batch in batches for e in batch}

        spans = observe_all(batches).spans()

        assert any(s.ts_start < e.ts_start and e.ts_stop < s.ts_stop for s in spans for e in observed.values())


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

    def test_the_span_is_drawn_at_the_full_width_of_its_windows(self, captured: Ingested) -> None:
        """POLL_1 recovered two gen-1 records inside gen 0's window, and the
        span is drawn over them rather than cut around them. Where the missing
        records ran is what the span leaves open; those two collections are
        drawn on the interpreter's own row, so a reader can see them."""
        gen0, gen1 = captured.windows_for((0, 0))[0], captured.windows_for((0, 1))[0]
        observed = [(e.ts_start, e.ts_stop) for e in build_batch(POLL_1) if e.ts_start < e.ts_stop]

        spans = captured.spans()

        assert len(spans) == 1
        assert (spans[0].ts_start, spans[0].ts_stop) == (
            min(gen0.ts_start, gen1.ts_start),
            max(gen0.ts_stop, gen1.ts_stop),
        )
        assert any(spans[0].ts_start < start < spans[0].ts_stop for start, _stop in observed)

    def test_the_whole_loss_lands_on_the_one_span(self, captured: Ingested) -> None:
        """Both generations' totals ride on it, each still the counters' own."""
        spans = captured.spans()

        assert spans[0].lost_count == {0: 76, 1: 5}

    def test_the_two_generations_merge_into_one_span(self, captured: Ingested) -> None:
        """Real data producing the shape ADR-0015 is about: both windows sit
        inside one tick and overlap."""
        merged = merge_windows(captured.windows_for((0, 0)) + captured.windows_for((0, 1)))

        assert len(merged) == 1
        assert merged[0].lost_count == {0: 76, 1: 5}


class TestARecordReadIncompleteThenComplete:
    """A slot caught mid-write is dropped, and arrives one poll late.

    `_is_complete` filters it before the cursor ever sees it, so the next poll
    returns it finished and fresh: emitted then, drawn where it ran, which is
    before that poll's window would otherwise open.

    Its `ts_stop` confirms rather than holes. The GC was inside that collection
    at the earlier read and nothing newer had finished, so a lost record would
    have been the newest one that read saw. Collections in an interpreter are
    serialized, so everything lost since ran after it ended.
    The window opens there, and no loss is attributed to the stretch before
    it.
    """

    def polls(self) -> tuple[Ingested, GCStatsInfo, list[GCStatsInfo]]:
        gen0 = build_run(4, gen=0, spacing_ns=44_000_000)
        done = build_run(1, gen=1, ts0=TS0 + 66_000_000)[0]
        # The same slot as the poll caught it: `ts_start` published, `ts_stop`
        # still carrying the value memcpy'd from the record before it.
        mid_write = msgspec.structs.replace(done, ts_stop=done.ts_start - 1_000)

        ingested = Ingested()
        ingested.poll([gen0[0], mid_write])
        ingested.poll([done, gen0[2], gen0[3]])
        return ingested, done, gen0

    def test_the_dropped_record_is_emitted_by_the_later_poll(self) -> None:
        ingested, done, _gen0 = self.polls()

        assert ingested[(0, 1)].last == done.collections
        assert ingested[(0, 1)].sampled_count == 1

    def test_it_opens_no_window_of_its_own(self) -> None:
        """Dropping it left the cursor untouched, so nothing looks lost."""
        ingested, _done, _gen0 = self.polls()

        assert ingested.windows_for((0, 1)) == []

    def test_the_window_opens_where_it_finished(self) -> None:
        ingested, done, gen0 = self.polls()

        gap = ingested.windows_for((0, 0))[0]

        assert gap.ts_start == done.ts_stop
        assert gap.ts_stop == gen0[2].ts_start

    def test_it_draws_as_one_span_starting_after_the_record(self) -> None:
        """The bound is what keeps the span off the stretch on its left, and
        it is evidence rather than geometry: the record proves nothing was
        lost before it ended."""
        ingested, done, gen0 = self.polls()

        assert [(s.ts_start, s.ts_stop) for s in ingested.spans()] == [(done.ts_stop, gen0[2].ts_start)]

    def test_the_window_does_not_reach_back_before_it(self) -> None:
        ingested, done, gen0 = self.polls()

        gap = ingested.windows_for((0, 0))[0]

        assert gap.ts_start > gen0[0].ts_stop
        assert gap.ts_start > done.ts_start

    def test_the_start_alone_confirms_even_if_the_record_never_returns(self) -> None:
        """The slot can be overwritten before the next read, and often is,
        which is the whole problem here. gcmon published and read `ts_start`,
        so the bound survives losing the record itself."""
        gen0 = build_run(4, gen=0, spacing_ns=44_000_000)
        gen1 = build_run(1, gen=1, ts0=TS0 + 66_000_000)[0]
        mid_write = msgspec.structs.replace(gen1, ts_stop=gen1.ts_start - 1_000)

        ingested = Ingested()
        ingested.poll([gen0[0], mid_write])
        ingested.poll([gen0[2], gen0[3]])  # the gen-1 slot is gone

        gap = ingested.windows_for((0, 0))[0]
        assert gap.ts_start == gen1.ts_start
        assert ingested.windows_for((0, 1)) == []

    def test_learning_where_it_ended_raises_the_bound_further(self) -> None:
        ingested, done, _gen0 = self.polls()

        gap = ingested.windows_for((0, 0))[0]

        assert gap.ts_start == done.ts_stop
        assert gap.ts_start > done.ts_start

    def test_the_loss_is_still_counted_in_full(self) -> None:
        """A narrower window changes where it is drawn, never how much."""
        ingested, _done, _gen0 = self.polls()

        assert sum(s.lost_count.get(0, 0) for s in ingested.spans()) == 1


class TestMergeWindows:
    def test_nothing_merges_to_nothing(self) -> None:
        assert merge_windows([]) == []

    def test_disjoint_windows_stay_apart(self) -> None:
        merged = merge_windows([window(ts_start=10, ts_stop=20), window(ts_start=30, ts_stop=40)])

        assert [(m.ts_start, m.ts_stop) for m in merged] == [(10, 20), (30, 40)]

    def test_crossing_windows_merge(self) -> None:
        """The shape that forced the dedicated track: gen 0 was the last
        record observed before the gap, gen 1 the first observed after it."""
        merged = merge_windows(
            [
                window(gen=0, ts_start=1, ts_stop=20, lost_count=76),
                window(gen=1, ts_start=2, ts_stop=25, lost_count=5),
            ]
        )

        assert len(merged) == 1
        assert (merged[0].ts_start, merged[0].ts_stop) == (1, 25)
        assert merged[0].lost_count == {0: 76, 1: 5}

    def test_a_nested_window_does_not_extend_the_span(self) -> None:
        merged = merge_windows(
            [window(gen=0, ts_start=1, ts_stop=100), window(gen=1, ts_start=10, ts_stop=20)],
        )

        assert (merged[0].ts_start, merged[0].ts_stop) == (1, 100)

    def test_touching_windows_merge(self) -> None:
        """Left apart they would draw two slices with nothing between them."""
        merged = merge_windows([window(ts_start=10, ts_stop=20), window(gen=1, ts_start=20, ts_stop=30)])

        assert len(merged) == 1
        assert (merged[0].ts_start, merged[0].ts_stop) == (10, 30)

    def test_input_order_does_not_matter(self) -> None:
        windows = [
            window(gen=1, ts_start=2, ts_stop=25, lost_count=5),
            window(gen=0, ts_start=1, ts_stop=20, lost_count=76),
            window(gen=2, ts_start=90, ts_stop=99, lost_count=1),
        ]

        assert merge_windows(windows) == merge_windows(list(reversed(windows)))

    def test_pause_sums_accumulate_per_generation(self) -> None:
        merged = merge_windows(
            [
                window(gen=0, ts_start=1, ts_stop=20, lost_pause_ns=700),
                window(gen=0, ts_start=15, ts_stop=30, lost_pause_ns=300),
                window(gen=1, ts_start=2, ts_stop=25, lost_pause_ns=50),
            ]
        )

        assert merged[0].lost_pause_ns == {0: 1000, 1: 50}

    def test_spans_come_back_in_time_order(self) -> None:
        merged = merge_windows([window(ts_start=50, ts_stop=60), window(ts_start=10, ts_stop=20)])

        assert [m.ts_start for m in merged] == [10, 50]


class TestMergeProperties:
    """Properties the emission side depends on, over a spread of shapes."""

    @pytest.mark.parametrize("shape", SHAPES)
    def test_merged_spans_are_pairwise_disjoint(self, shape: list[tuple[int, int, int]]) -> None:
        """The whole reason for merging: a track is a stack, so two spans on
        it must nest or not touch."""
        merged = merge_windows([window(gen=g, ts_start=a, ts_stop=b) for g, a, b in shape])

        for earlier, later in pairwise(merged):
            assert earlier.ts_stop < later.ts_start

    @pytest.mark.parametrize("shape", SHAPES)
    def test_every_window_lands_inside_exactly_one_span(self, shape: list[tuple[int, int, int]]) -> None:
        """What makes attribution of the per-generation counts unambiguous."""
        windows = [window(gen=g, ts_start=a, ts_stop=b) for g, a, b in shape]
        merged = merge_windows(windows)

        for w in windows:
            containing = [m for m in merged if m.ts_start <= w.ts_start and w.ts_stop <= m.ts_stop]
            assert len(containing) == 1

    @pytest.mark.parametrize("shape", SHAPES)
    def test_no_count_is_dropped_or_double_counted(self, shape: list[tuple[int, int, int]]) -> None:
        windows = [window(gen=g, ts_start=a, ts_stop=b, lost_count=g + 1) for g, a, b in shape]
        merged = merge_windows(windows)

        total = sum(sum(m.lost_count.values()) for m in merged)
        assert total == sum(w.lost_count for w in windows)


class TestToLossMsg:
    def test_carries_the_span_and_its_per_generation_totals(self) -> None:
        msg = to_loss_msg(1, MergedLoss(ts_start=10, ts_stop=99, lost_count={0: 76, 1: 5}, lost_pause_ns={0: 81, 1: 7}))

        assert (msg.iid, msg.ts_start, msg.ts_stop) == (1, 10, 99)
        assert (msg.lost_gen_0, msg.lost_gen_1) == (76, 5)
        assert (msg.lost_pause_gen_0, msg.lost_pause_gen_1) == (81, 7)

    def test_a_generation_outside_the_span_reads_zero(self) -> None:
        """The span is real and gen 2 lost nothing in it, which is a different
        statement from gen 2 being unknown."""
        msg = to_loss_msg(0, MergedLoss(ts_start=10, ts_stop=99, lost_count={0: 76}))

        assert msg.lost_gen_2 == 0
        assert msg.lost_pause_gen_2 == 0

    def test_a_merged_capture_flattens(self, captured: Ingested) -> None:
        merged = merge_windows(captured.windows_for((0, 0)) + captured.windows_for((0, 1)))
        msg = to_loss_msg(0, merged[0])

        assert (msg.lost_gen_0, msg.lost_gen_1, msg.lost_gen_2) == (76, 5, 0)


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

        assert sum(span.lost_pause_ns.values()) <= span.ts_stop - span.ts_start

    def test_the_spans_are_disjoint_and_in_order(self) -> None:
        """They share the loss track, so they have to stay a stack."""
        spans = self.polls().spans()

        assert all(a.ts_stop <= b.ts_start for a, b in pairwise(spans))

    def test_the_loss_rides_whole_on_the_one_span(self) -> None:
        """Nothing says which side of the observed collection each lost record
        fell, and the span no longer guesses: it carries both."""
        spans = self.polls().spans()

        assert [s.lost_count.get(0, 0) for s in spans] == [2]

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
        assert spans[0].lost_count == {0: 1}
        assert sum(spans[0].lost_pause_ns.values()) > 0

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

        assert ingested.spans()[0].lost_count == {0: 1}


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

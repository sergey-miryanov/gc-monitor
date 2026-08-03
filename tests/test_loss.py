"""Tests for reconstructing what a poll could not observe.

Two kinds of input here. The synthetic runs below carry a cumulative
``duration`` the way a real target does, so they can check the arithmetic
against ground truth: build a full run, show the accumulator only what
survives a ring of a given size, and compare what it reconstructs to what
actually happened. The capture fixture from ``test_monitor_cursor`` carries
no durations, so it checks gap counts against real slot data instead.
"""

from collections.abc import Callable, Iterator, Sequence
from itertools import groupby, pairwise

import pytest

from gcmon.data import GCStatsInfo, secs_to_ns
from gcmon.loss import KeyAccumulator, LossWindow, MergedLoss, merge_by_interpreter, merge_windows, to_loss_msg
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


def fold_singly(events: Sequence[GCStatsInfo]) -> KeyAccumulator:
    """The same records, one single-record run at a time."""
    accumulator = KeyAccumulator()
    for event in events:
        accumulator.observe_batch([event])
    return accumulator


def ingest(cursors: dict[tuple[int, int], KeyAccumulator], batch: Sequence[GCStatsInfo]) -> None:
    """Mirror what ``EventsMonitor._ingest`` will do to a whole ring buffer.

    Slot order is not time order, so a helper that walked the batch as it
    came would seed ``first`` from whichever record sat at the ring's write
    position. Sort each ring back into counter order, drop what the cursor
    has already passed, and hand the rest over as one run.
    """
    ordered = sorted(
        (event for event in batch if event.ts_start < event.ts_stop),
        key=lambda e: (e.iid, e.gen, e.ts_start),
    )

    for key, group in groupby(ordered, key=lambda e: (e.iid, e.gen)):
        accumulator = cursors.setdefault(key, KeyAccumulator())
        seen = accumulator.last
        run: list[GCStatsInfo] = []
        for event in group:
            # Already emitted, or the copy the target makes of a record
            # ahead of overwriting it.
            if event.collections <= seen:
                continue
            seen = event.collections
            run.append(event)

        accumulator.observe_batch(run)


def observe_all(
    cursors: dict[tuple[int, int], KeyAccumulator], batches: Iterator[Sequence[GCStatsInfo]]
) -> dict[tuple[int, int], KeyAccumulator]:
    for batch in batches:
        ingest(cursors, batch)
    return cursors


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
def captured() -> dict[tuple[int, int], KeyAccumulator]:
    """The verbatim two-poll capture, ingested the way the monitor would."""
    return observe_all({}, iter([build_batch(POLL_0), build_batch(POLL_1)]))


class TestEmptyAccumulator:
    def test_reports_nothing(self, accumulator: KeyAccumulator) -> None:
        assert accumulator.exact_count == 0
        assert accumulator.exact_pause_ns == 0
        assert accumulator.lost_count == 0
        assert accumulator.windows == []

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
        accumulator.observe_batch(
            [create_mock_stats_item(collections=42, ts_start=1_000, ts_stop=1_700, duration=0.0007)]
        )

        assert accumulator.exact_count == 1
        assert accumulator.exact_pause_ns == 700
        assert accumulator.sampled_pause_ns == 700
        assert accumulator.windows == []

    def test_two_adjacent_records_leave_no_gap(self, accumulator: KeyAccumulator) -> None:
        accumulator.observe_batch(build_run(2))

        assert accumulator.exact_count == 2
        assert accumulator.lost_count == 0
        assert accumulator.windows == []

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
        accumulator.observe_batch([events[2]])

        assert len(accumulator.windows) == 1
        gap = accumulator.windows[0]
        assert gap.lost_count == 1
        assert gap.lost_pause_ns == events[1].ts_stop - events[1].ts_start

    def test_the_window_is_bounded_by_observed_records(self, accumulator: KeyAccumulator) -> None:
        events = build_run(6)
        accumulator.observe_batch([events[0]])
        accumulator.observe_batch([events[4]])

        gap = accumulator.windows[0]
        assert gap.ts_start == events[0].ts_stop
        assert gap.ts_stop == events[4].ts_start

    def test_the_window_carries_its_generation(self, accumulator: KeyAccumulator) -> None:
        events = build_run(3, gen=1)
        accumulator.observe_batch([events[0]])
        accumulator.observe_batch([events[2]])

        assert accumulator.windows[0].gen == 1

    def test_a_lossless_run_opens_none(self, accumulator: KeyAccumulator) -> None:
        accumulator.observe_batch(build_run(50))

        assert accumulator.windows == []
        assert accumulator.coverage == 1.0
        assert accumulator.scale_factor == pytest.approx(1.0, abs=1e-9)

    def test_no_window_before_the_first_record_or_after_the_last(self, accumulator: KeyAccumulator) -> None:
        events = build_run(30)
        accumulator.observe_batch(events[10:20])

        assert accumulator.windows == []


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
        batched.observe_batch(events[12:])

        assert [w.lost_count for w in batched.windows] == [7]
        assert batched == fold_singly(events[:5] + events[12:])

    def test_a_hole_inside_a_run_goes_unnoticed(self) -> None:
        """Pinning an accepted risk, not a wanted behaviour. A run is trusted
        to be contiguous because a ring holds consecutive records; only a read
        torn by two collections landing inside one ~1 KB copy could break that.
        The ends still give the right counts, but nothing carries the hole's
        pause, so the §4 invariant does not hold. See ADR-0015."""
        events = build_run(10)
        torn = events[:4] + events[6:]
        batched = KeyAccumulator()

        batched.observe_batch(torn)

        assert batched.lost_count == 2
        assert batched.windows == []
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
        acc = observe_all({}, ring_polls(events, capacity, per_tick))[(0, 0)]

        assert acc.exact_count == acc.last - acc.first + 1
        assert acc.exact_pause_ns == true_pause_ns(events, acc.first, acc.last)

    @pytest.mark.parametrize(("capacity", "per_tick"), [(11, 87), (3, 8), (1, 5), (11, 11)])
    def test_the_invariant_holds(self, capacity: int, per_tick: int) -> None:
        """Exact pause time is what gcmon saw plus what every window says it
        missed. This is the one assertion that catches a fencepost error, a
        clock mismatch between ``duration`` and the timestamps, and a wrong
        window in a single check."""
        acc = observe_all({}, ring_polls(build_run(400), capacity, per_tick))[(0, 0)]

        lost = sum(w.lost_pause_ns for w in acc.windows)
        assert acc.exact_pause_ns == acc.sampled_pause_ns + lost

    def test_coverage_approaches_the_ring_ratio(self) -> None:
        """11 slots against 87 collections per tick keeps 11 of every 87, once
        the run is long enough to drown the first tick. That one is narrower:
        its span starts at the oldest slot still in the ring, so the 76
        records lost before gcmon ever looked fall outside the span."""
        acc = observe_all({}, ring_polls(build_run(8_700), 11, 87))[(0, 0)]

        assert acc.coverage == pytest.approx(11 / 87, rel=0.02)
        assert acc.coverage == acc.sampled_count / acc.exact_count

    def test_lost_count_matches_the_windows(self) -> None:
        acc = observe_all({}, ring_polls(build_run(400), 11, 87))[(0, 0)]

        assert acc.lost_count == sum(w.lost_count for w in acc.windows)

    def test_scale_factor_corrects_a_sampled_sum(self) -> None:
        acc = observe_all({}, ring_polls(build_run(400), 11, 87))[(0, 0)]

        corrected = acc.sampled_pause_ns * acc.scale_factor
        assert corrected == pytest.approx(acc.exact_pause_ns, rel=1e-9)


class TestCaptureFixture:
    """Gap counts against the verbatim two-poll capture in test_monitor_cursor.

    The capture recorded no ``duration``, so every record carries the factory
    default and only counts mean anything here. The pause arithmetic is
    covered by the synthetic runs above.
    """

    def test_gen_0_lost_seventy_six_records(self, captured: dict[tuple[int, int], KeyAccumulator]) -> None:
        acc = captured[(0, 0)]

        assert (acc.first, acc.last) == (466, 563)
        assert [w.lost_count for w in acc.windows] == [76]
        assert acc.sampled_count == 22

    def test_gen_1_lost_five_records(self, captured: dict[tuple[int, int], KeyAccumulator]) -> None:
        acc = captured[(0, 1)]

        assert (acc.first, acc.last) == (41, 51)
        assert [w.lost_count for w in acc.windows] == [5]

    def test_an_unchanged_generation_loses_nothing(self, captured: dict[tuple[int, int], KeyAccumulator]) -> None:
        acc = captured[(0, 2)]

        assert acc.sampled_count == 1
        assert acc.windows == []

    def test_the_window_spans_the_unobserved_interval(self, captured: dict[tuple[int, int], KeyAccumulator]) -> None:
        """From the newest gen-0 record in the first poll to the oldest in the
        second, 90.0 ms of a 100 ms tick. Both bounds come from time order,
        not slot order: 563 sits at the head of the second batch and 553 at
        its tail."""
        gap = captured[(0, 0)].windows[0]

        assert gap.ts_start == 294787154918900  # collections=476, newest in POLL_0
        assert gap.ts_stop == 294787244879600  # collections=553, oldest in POLL_1
        assert gap.ts_stop - gap.ts_start == pytest.approx(90_000_000, rel=0.01)

    def test_the_two_generations_merge_into_one_span(self, captured: dict[tuple[int, int], KeyAccumulator]) -> None:
        """Real data producing the shape §5.1 of the spec is about: both
        windows sit inside one tick and overlap."""
        merged = merge_by_interpreter(captured)

        assert list(merged) == [0]
        assert len(merged[0]) == 1
        assert merged[0][0].lost_count == {0: 76, 1: 5}


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

    def test_a_merged_capture_flattens(self, captured: dict[tuple[int, int], KeyAccumulator]) -> None:
        merged = merge_by_interpreter(captured)
        msg = to_loss_msg(0, merged[0][0])

        assert (msg.lost_gen_0, msg.lost_gen_1, msg.lost_gen_2) == (76, 5, 0)


class TestMergeByInterpreter:
    def test_generations_of_one_interpreter_merge(self) -> None:
        cursors = {
            (0, 0): KeyAccumulator(windows=[window(gen=0, ts_start=1, ts_stop=20, lost_count=76)]),
            (0, 1): KeyAccumulator(windows=[window(gen=1, ts_start=2, ts_stop=25, lost_count=5)]),
        }

        merged = merge_by_interpreter(cursors)

        assert list(merged) == [0]
        assert merged[0] == [MergedLoss(ts_start=1, ts_stop=25, lost_count={0: 76, 1: 5}, lost_pause_ns={0: 0, 1: 0})]

    def test_interpreters_stay_apart(self) -> None:
        """Each interpreter draws on its own track, so its spans never share
        a stack with another's."""
        cursors = {
            (0, 0): KeyAccumulator(windows=[window(ts_start=1, ts_stop=20)]),
            (1, 0): KeyAccumulator(windows=[window(ts_start=2, ts_stop=25)]),
        }

        merged = merge_by_interpreter(cursors)

        assert sorted(merged) == [0, 1]
        assert [(m.ts_start, m.ts_stop) for m in merged[0]] == [(1, 20)]
        assert [(m.ts_start, m.ts_stop) for m in merged[1]] == [(2, 25)]

    def test_an_interpreter_with_no_windows_is_absent(self) -> None:
        cursors = {(0, 0): KeyAccumulator(), (0, 1): KeyAccumulator()}

        assert merge_by_interpreter(cursors) == {}

    def test_no_cursors_at_all(self) -> None:
        assert merge_by_interpreter({}) == {}

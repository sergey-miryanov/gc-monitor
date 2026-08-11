"""What the loss arithmetic recovers, against a target whose every collection
is known.

Every other test of the loss code hands the monitor batches somebody wrote by
hand, which can only check that the arithmetic does what its author expected.
This one starts from `SSL_CONTEXT_SIZE`, a capture of every collection a real
target ran, puts a ring buffer and a poll clock in front of it, and asks
whether what gcmon reconstructs from the wreckage matches the target. The
capture is the answer key; the ring is what hides it.

The ring model follows `add_stats` in CPython's `Python/gc.c`: it advances the
index before writing, so record `k` lands in slot `k % size`, and it publishes
`ts_stop` last, so a record becomes readable when its collection ends.
`read_gc_stats` then copies the whole `struct gc_stats` in one read and walks
it generation-major, each generation's slots in index order. `TestTheRingModel`
holds the model to the verbatim hardware capture in `test_monitor_cursor`, so a
model that drifts from the extension fails here rather than quietly making
every claim below easier.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import combinations, pairwise
from typing import override
from unittest.mock import patch

import pytest

from gcmon.data import GCStatsInfo, lost_to
from gcmon.exporters.exporter import EventsExporter
from gcmon.monitor import EventsMonitor
from gcmon.poll_status import PollStatus
from gcmon.protocol import TGCStatsInfo, TInstantMsg, TLossMsg
from gcmon.stats import StreamingStats
from gcmon.target_process import ExternalProcess
from tests.captures import SSL_CONTEXT_SIZE
from tests.test_monitor_cursor import POLL_0

PID = 33328
IID = 0

# CPython 3.15's default build. A parameter rather than a constant, so a test
# can ask what the free-threaded geometry of one slot per ring would cost.
RING_SIZES = {0: 11, 1: 3, 2: 3}
FREE_THREADED_SIZES = {0: 1, 1: 1, 2: 1}

MS = 1_000_000

# Poll periods the replays below run at, in ms. 250 outruns the target, 1000
# blinds gen 0 alone, and 3000 blinds gen 0 and gen 1 together, which is the
# only shape that puts two windows on one loss row at one instant.
LOSSLESS_MS = 250
ONE_GENERATION_MS = 1000
TWO_GENERATIONS_MS = 3000


class Recorder(EventsExporter):
    """Every record the monitor exported, split by kind."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[TGCStatsInfo] = []
        self.losses: list[TLossMsg] = []

    @override
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        self.records.append(item)

    @override
    def add_loss_event(self, pid: int, item: TLossMsg) -> None:
        self.losses.append(item)

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        pass

    @override
    def close(self) -> None:
        pass


def capture_records(
    capture: Sequence[tuple[int, int, int, int, float]] = SSL_CONTEXT_SIZE,
) -> dict[int, list[GCStatsInfo]]:
    """The capture as records, one list per generation, in counter order."""
    per_gen: dict[int, list[GCStatsInfo]] = {}
    for gen, collections, ts_start, ts_stop, duration in capture:
        per_gen.setdefault(gen, []).append(
            GCStatsInfo(
                gen=gen,
                iid=IID,
                ts_start=ts_start,
                ts_stop=ts_stop,
                heap_size=0,
                collections=collections,
                collected=0,
                uncollectable=0,
                candidates=0,
                duration=duration,
            )
        )
    for records in per_gen.values():
        records.sort(key=lambda record: record.collections)
    return per_gen


def empty_slot(gen: int) -> GCStatsInfo:
    """A slot the target never wrote.

    Zeroed but for the ring it belongs to, which is how the extension returns
    one: `read_gc_stats` sets `gen` from the loop it is in rather than from the
    slot. `_is_complete` rejects it.
    """
    return GCStatsInfo(
        gen=gen,
        iid=IID,
        ts_start=0,
        ts_stop=0,
        heap_size=0,
        collections=0,
        collected=0,
        uncollectable=0,
        candidates=0,
        duration=0.0,
    )


def ring_at(records: Sequence[GCStatsInfo], gen: int, size: int, ts: int) -> list[GCStatsInfo]:
    """One ring's slots as they stand at *ts*, in slot order."""
    written = [record for record in records if record.ts_stop <= ts]
    slots = [empty_slot(gen) for _ in range(size)]
    for record in written[-size:]:
        slots[record.collections % size] = record
    return slots


def poll_batches(
    per_gen: dict[int, list[GCStatsInfo]], sizes: dict[int, int], interval_ns: int, skew_ns: int = 0
) -> Iterator[list[GCStatsInfo]]:
    """One batch per wake of a clock ticking every *interval_ns*.

    The first wake lands on the first collection in the capture, so the run
    starts against a ring holding something. The last lands past the final
    collection, so no record goes unread for want of a poll to read it.

    *skew_ns* tears the read. One `ReadRemoteMemory` copies the whole
    `struct gc_stats` without stopping the target, so the rings in it can
    reflect different instants; `struct gc_stats` holds gen 0 first, so a copy
    running up through memory takes the older generations later. Each ring is
    read `skew_ns` after the one below it. Zero is the untorn read, and the
    direction is a model of one plausible copy and not a guarantee: nothing
    below rests on which ring comes out newer, only on their disagreeing.
    """
    last = max(record.ts_stop for records in per_gen.values() for record in records)

    ts = min(record.ts_stop for records in per_gen.values() for record in records)
    while ts <= last + interval_ns:
        batch: list[GCStatsInfo] = []
        for gen in sorted(per_gen):
            batch.extend(ring_at(per_gen[gen], gen, sizes[gen], ts + skew_ns * gen))
        yield batch
        ts += interval_ns


@dataclass
class Replay:
    """One run of the monitor over the capture, with the answer key beside it."""

    truth: dict[int, list[GCStatsInfo]]
    recorder: Recorder
    stats: StreamingStats
    polls: int = 0
    _read: dict[int, set[int]] = field(default_factory=dict)

    def read(self, gen: int) -> set[int]:
        """The counters gcmon exported a record for."""
        if not self._read:
            for record in self.recorder.records:
                self._read.setdefault(record.gen, set()).add(record.collections)
        return self._read.get(gen, set())

    def windows(self, gen: int) -> list[TLossMsg]:
        return [loss for loss in self.recorder.losses if loss.gen == gen]

    def lost(self, gen: int) -> set[int]:
        """The counters the windows claim, expanded from their ranges."""
        return {
            collections
            for loss in self.windows(gen)
            for collections in range(loss.lost_from, lost_to(loss.lost_from, loss.lost_count) + 1)
        }

    def span(self, gen: int) -> range:
        """The collections between the first and last gcmon read, inclusive.

        What ran before gcmon attached is outside anything it can claim, so
        this is the interval every check below is scoped to.
        """
        read = self.read(gen)
        return range(min(read), max(read) + 1)

    def truth_pause_ns(self, gen: int, collections: Sequence[int] | set[int]) -> int:
        """What those collections really cost, from the capture's own
        timestamps rather than from the cumulative `duration` gcmon works
        off."""
        by_counter = {record.collections: record for record in self.truth[gen]}
        return sum(by_counter[c].ts_stop - by_counter[c].ts_start for c in collections)


def replay(interval_ms: float, sizes: dict[int, int] = RING_SIZES, skew_ms: float = 0) -> Replay:
    """Poll the capture through a ring of *sizes* every *interval_ms*."""
    truth = capture_records()
    batches = list(poll_batches(truth, sizes, int(interval_ms * MS), int(skew_ms * MS)))

    recorder = Recorder()
    stats = StreamingStats()
    monitor = EventsMonitor(ExternalProcess(pid=PID), recorder, stats)
    reads = iter(batches)

    def one_read(pid: int, all_interpreters: bool = True) -> list[GCStatsInfo]:
        return list(next(reads))

    with patch("gcmon.monitor.get_gc_stats", side_effect=one_read):
        for _ in batches:
            assert monitor.poll(PID) is PollStatus.OK

    return Replay(truth=truth, recorder=recorder, stats=stats, polls=len(batches))


LOSSY_MS = [ONE_GENERATION_MS, TWO_GENERATIONS_MS]


@pytest.fixture(scope="module")
def lossless() -> Replay:
    return replay(LOSSLESS_MS)


@pytest.fixture(scope="module")
def lossy() -> dict[float, Replay]:
    return {interval_ms: replay(interval_ms) for interval_ms in LOSSY_MS}


class TestTheRingModel:
    """The model against the extension, before anything is claimed with it."""

    def test_it_lays_out_the_verbatim_hardware_capture(self) -> None:
        """`POLL_0` is a real `get_gc_stats` return, rotated the way the target
        left it. Rebuilding it from its own records slot by slot is what says
        the model here is CPython's ring and not a plausible one: the counters
        alone do not fix an order, and `% size` off by one still produces a
        ring holding the same records in the wrong places.
        """
        per_gen: dict[int, list[GCStatsInfo]] = {}
        for gen, collections, ts_start, ts_stop in POLL_0:
            if ts_start < ts_stop:
                per_gen.setdefault(gen, []).append(
                    GCStatsInfo(
                        gen=gen,
                        iid=IID,
                        ts_start=ts_start,
                        ts_stop=ts_stop,
                        heap_size=0,
                        collections=collections,
                        collected=0,
                        uncollectable=0,
                        candidates=0,
                        duration=0.0,
                    )
                )
        newest = max(ts_stop for _gen, _c, _ts, ts_stop in POLL_0)

        rebuilt = [
            (slot.gen, slot.collections, slot.ts_start, slot.ts_stop)
            for gen in (0, 1, 2)
            for slot in ring_at(sorted(per_gen.get(gen, []), key=lambda r: r.collections), gen, RING_SIZES[gen], newest)
        ]

        assert rebuilt == POLL_0

    def test_a_ring_holds_only_its_newest_records(self) -> None:
        gen_0 = capture_records()[0]

        held = ring_at(gen_0, 0, RING_SIZES[0], gen_0[49].ts_stop)

        assert {slot.collections for slot in held} == set(range(40, 51))

    def test_an_unwritten_slot_still_names_its_generation(self) -> None:
        """Gen 2 collected twice in the whole capture, so its ring never
        fills. `record_ring_geometry` counts those slots and would read the
        ring as shorter than it is if they came back under another gen."""
        held = ring_at(capture_records()[2], 2, RING_SIZES[2], 0)

        assert [slot.gen for slot in held] == [2, 2, 2]
        assert [slot.collections for slot in held] == [0, 0, 0]


class TestTheReplayLosesWhatItClaimsTo:
    """Controls. Every check below is vacuous against a replay that read
    everything, and a replay silently reading everything is the likeliest way
    for this file to stop testing the loss code at all."""

    def test_polling_faster_than_the_target_collects_loses_nothing(self, lossless: Replay) -> None:
        assert lossless.recorder.losses == []
        assert [len(lossless.read(gen)) for gen in (0, 1, 2)] == [230, 20, 2]

    def test_one_generation_goes_blind(self, lossy: dict[float, Replay]) -> None:
        run = lossy[ONE_GENERATION_MS]

        assert run.stats.coverage(PID, 0) < 0.6
        assert [len(run.windows(gen)) > 0 for gen in (0, 1, 2)] == [True, False, False]

    def test_two_generations_go_blind_over_the_same_stretch(self, lossy: dict[float, Replay]) -> None:
        """What puts two spans on one loss row at overlapping times, which is
        the precondition for anything below to have a nesting to check.

        Overlap, not a shared left edge: where the edges land is the thing
        under test, so a control resting on it would fail for the same reason
        as its subject and leave the suite looking better than it is.
        """
        run = lossy[TWO_GENERATIONS_MS]

        overlapping = [
            (a.gen, b.gen)
            for a, b in combinations(run.recorder.losses, 2)
            if a.gen != b.gen and a.ts_start < b.ts_stop and b.ts_start < a.ts_stop
        ]

        assert overlapping, "no two generations were blind over the same stretch"

    def test_a_ring_of_one_slot_loses_almost_everything(self) -> None:
        """The free-threaded geometry, where a poll can keep at most the last
        collection of each generation."""
        run = replay(LOSSLESS_MS, FREE_THREADED_SIZES)

        assert run.stats.coverage(PID, 0) < 0.25


@pytest.mark.parametrize("interval_ms", LOSSY_MS)
class TestEveryCollectionIsChargedOnce:
    """The claim `LossWindow.lost_from` exists to support: over the span gcmon
    observed, every collection the target ran is either a record gcmon drew or
    a counter inside exactly one window, and never both."""

    def test_nothing_is_charged_twice(self, interval_ms: float, lossy: dict[float, Replay]) -> None:
        run = lossy[interval_ms]

        for gen in (0, 1, 2):
            assert run.read(gen) & run.lost(gen) == set(), f"gen {gen} drew a record it also called lost"

    def test_nothing_is_charged_nowhere(self, interval_ms: float, lossy: dict[float, Replay]) -> None:
        run = lossy[interval_ms]

        for gen in (0, 1, 2):
            assert set(run.span(gen)) - run.read(gen) - run.lost(gen) == set()

    def test_nothing_is_charged_outside_the_span(self, interval_ms: float, lossy: dict[float, Replay]) -> None:
        """A window reaching behind the first record gcmon read would be
        claiming collections that ran before it attached."""
        run = lossy[interval_ms]

        for gen in (0, 1, 2):
            assert run.lost(gen) <= set(run.span(gen))

    def test_the_windows_do_not_overlap_each_other(self, interval_ms: float, lossy: dict[float, Replay]) -> None:
        run = lossy[interval_ms]

        for gen in (0, 1, 2):
            claimed = sum(loss.lost_count for loss in run.windows(gen))
            assert claimed == len(run.lost(gen)), f"gen {gen} windows claim overlapping counters"


@pytest.mark.parametrize("interval_ms", LOSSY_MS)
class TestTheTotalsAreExact:
    """`--stats` promises counts and sums that cover every collection, read or
    not. The capture says what those are."""

    def test_the_count_covers_the_whole_span(self, interval_ms: float, lossy: dict[float, Replay]) -> None:
        run = lossy[interval_ms]

        for gen in (0, 1, 2):
            assert run.stats.exact_count(PID, gen) == len(run.span(gen))

    def test_the_pause_sum_matches_the_target(self, interval_ms: float, lossy: dict[float, Replay]) -> None:
        """Within a microsecond over eleven seconds. The reconstruction adds up
        deltas of a cumulative double of seconds; the capture carries integer
        nanoseconds. They cannot agree exactly and nothing should claim they
        do."""
        run = lossy[interval_ms]

        for gen in (0, 1, 2):
            truth = run.truth_pause_ns(gen, run.span(gen))
            assert run.stats.exact_pause_ns(PID, gen) == pytest.approx(truth, abs=1_000)

    def test_the_lost_pause_is_the_pause_of_the_lost_records(
        self, interval_ms: float, lossy: dict[float, Replay]
    ) -> None:
        """Not a share of the window, and not the window's width: the time the
        collections nobody saw actually spent collecting."""
        run = lossy[interval_ms]

        for gen in (0, 1, 2):
            truth = run.truth_pause_ns(gen, run.lost(gen))
            assert run.stats.lost_pause_ns(PID, gen) == pytest.approx(truth, abs=1_000)

    def test_coverage_is_the_share_actually_read(self, interval_ms: float, lossy: dict[float, Replay]) -> None:
        run = lossy[interval_ms]

        for gen in (0, 1, 2):
            assert run.stats.coverage(PID, gen) == pytest.approx(len(run.read(gen)) / len(run.span(gen)))


@pytest.mark.parametrize("interval_ms", LOSSY_MS)
class TestTheWindowsAreDrawable:
    """Geometry, over the same replays. The loss row is a Perfetto stack and
    nothing downstream complains when it is built wrong."""

    def test_every_window_bounds_an_interval(self, interval_ms: float, lossy: dict[float, Replay]) -> None:
        run = lossy[interval_ms]

        assert [loss for loss in run.recorder.losses if loss.ts_start >= loss.ts_stop] == []
        assert [run.stats.undrawable_count(PID, gen) for gen in (0, 1, 2)] == [0, 0, 0]

    def test_one_generation_windows_never_overlap_in_time(self, interval_ms: float, lossy: dict[float, Replay]) -> None:
        """Across polls they are disjoint: a poll opens at or after the newest
        record the poll before it saw."""
        run = lossy[interval_ms]

        for gen in (0, 1, 2):
            ordered = sorted(run.windows(gen), key=lambda loss: loss.ts_start)
            assert all(a.ts_stop <= b.ts_start for a, b in pairwise(ordered))

    def test_overlapping_windows_nest_instead_of_crossing(self, interval_ms: float, lossy: dict[float, Replay]) -> None:
        """The property the loss row is a stack because of, and the reason the
        left edge is `read_bound_per_interpreter` rather than each ring's own
        last record. One read covers all of an interpreter's generations, so
        every window a poll opens starts at the same instant and they can only
        differ in where each generation's next record sits. Edges taken per
        ring would stagger, and a staggered edge with a wider right edge
        crosses its neighbour, which no stack can hold.
        """
        run = lossy[interval_ms]

        for a, b in combinations(run.recorder.losses, 2):
            if a.iid != b.iid or a.ts_stop <= b.ts_start or b.ts_stop <= a.ts_start:
                continue
            assert a.ts_start == b.ts_start, (
                f"gen {a.gen} and gen {b.gen} overlap from different left edges: {a.ts_start} and {b.ts_start}"
            )

    def test_windows_sharing_a_left_edge_go_out_widest_first(
        self, interval_ms: float, lossy: dict[float, Replay]
    ) -> None:
        """An END closes the most recently opened slice, so the emission order
        at one instant is what decides which generation contains which."""
        run = lossy[interval_ms]

        by_edge: dict[tuple[int, int], list[int]] = {}
        for loss in run.recorder.losses:
            by_edge.setdefault((loss.iid, loss.ts_start), []).append(loss.ts_stop)

        for edge, stops in by_edge.items():
            assert stops == sorted(stops, reverse=True), f"{edge} emitted inside out"

    def test_a_window_reaches_its_own_generations_next_record(
        self, interval_ms: float, lossy: dict[float, Replay]
    ) -> None:
        """The right edge is the last thing two polls prove about that ring:
        the first record read after the gap."""
        run = lossy[interval_ms]

        for gen in (0, 1, 2):
            starts = {record.collections: record.ts_start for record in run.truth[gen]}
            for loss in run.windows(gen):
                after = lost_to(loss.lost_from, loss.lost_count) + 1
                assert loss.ts_stop == starts[after]


class TestATornRead:
    """A window whose bounds describe no interval, and what survives it.

    The read is one copy of the whole `struct gc_stats` taken while the target
    runs, so the rings in it can disagree about when "now" is. A poll that
    takes gen 1 later than gen 0 can set a read bound after a collection the
    next poll reads on gen 0, and the window between them then ends before it
    starts.

    `LossWindow.is_drawable` splits what that costs. The bounds are timestamps
    and they are wrong; `lost_count` and `lost_from` are subtractions of the
    ring's own cumulative counters with no timestamp anywhere in them, so they
    are right regardless. `_ingest` records the loss and skips only the span.
    """

    # Found by sweeping the capture: a poll period the ring survives almost
    # every time, so the one window that does open is a single collection wide
    # and a read taken 20 ms apart across the rings is enough to invert it.
    # A wider window has room for the skew and draws normally.
    INTERVAL_MS = 330
    SKEW_MS = 20

    @pytest.fixture(scope="class")
    def torn(self) -> Replay:
        return replay(self.INTERVAL_MS, skew_ms=self.SKEW_MS)

    @pytest.fixture(scope="class")
    def untorn(self) -> Replay:
        return replay(self.INTERVAL_MS)

    def test_the_same_run_read_whole_draws_the_window(self, untorn: Replay) -> None:
        """The control that makes this a test of the tear and not of the poll
        period: read without skew, the same loss opens a window that draws."""
        assert untorn.stats.lost_count(PID, 0) == 1
        assert [untorn.stats.undrawable_count(PID, gen) for gen in (0, 1, 2)] == [0, 0, 0]
        assert len(untorn.recorder.losses) == 1

    def test_the_tear_leaves_a_window_that_cannot_be_drawn(self, torn: Replay) -> None:
        assert torn.stats.undrawable_count(PID, 0) == 1

    def test_no_span_is_drawn_for_it(self, torn: Replay) -> None:
        """A span with `ts_stop` at or before `ts_start` would draw as an
        invisible sliver, or make the loss row stop nesting."""
        assert torn.recorder.losses == []

    def test_the_count_is_kept_anyway(self, torn: Replay) -> None:
        """`--stats` covers every collection whether or not a bar came of it."""
        assert torn.stats.lost_count(PID, 0) == 1
        assert torn.stats.exact_count(PID, 0) == len(torn.span(0))

    def test_the_lost_pause_is_kept_anyway(self, torn: Replay) -> None:
        missing = set(torn.span(0)) - torn.read(0)

        assert torn.stats.lost_pause_ns(PID, 0) == torn.truth_pause_ns(0, missing)

    def test_the_totals_still_match_the_target(self, torn: Replay) -> None:
        for gen in (0, 1, 2):
            truth = torn.truth_pause_ns(gen, torn.span(gen))
            assert torn.stats.exact_pause_ns(PID, gen) == pytest.approx(truth, abs=1_000)

    def test_coverage_still_counts_the_undrawn_loss(self, torn: Replay) -> None:
        """`undrawable_count` is a count of spans, not of collections, and must
        stay out of every figure that already carries the loss it measured."""
        assert torn.stats.coverage(PID, 0) == pytest.approx(len(torn.read(0)) / len(torn.span(0)))


class TestAMidWriteSlot:
    """`add_stats` is not atomic, so a poll can catch a slot part-written.

    It memcpy's the record it is about to overwrite into the new slot, then
    stores the new `ts_start`, then increments `collections`, and publishes
    `ts_stop` last. A reader therefore sees one of two things it must not take
    at face value, and `_ingest` guards both.
    """

    def batch_at(self, ts: int, sizes: dict[int, int] = RING_SIZES) -> list[GCStatsInfo]:
        truth = capture_records()
        return [slot for gen in sorted(truth) for slot in ring_at(truth[gen], gen, sizes[gen], ts)]

    def test_a_half_written_slot_is_not_read(self) -> None:
        """After the `ts_start` store and before the `ts_stop` one, the slot
        carries the new record's start over the previous record's stop. The
        start is the later of the two, which is what `_is_complete` keys on."""
        truth = capture_records()
        settled = truth[0][20]
        in_flight = truth[0][21]
        batch = self.batch_at(settled.ts_stop)
        half_written = GCStatsInfo(
            gen=0,
            iid=IID,
            ts_start=in_flight.ts_start,
            ts_stop=truth[0][10].ts_stop,
            heap_size=0,
            collections=in_flight.collections,
            collected=0,
            uncollectable=0,
            candidates=0,
            duration=truth[0][10].duration,
        )
        batch[in_flight.collections % RING_SIZES[0]] = half_written

        recorder = Recorder()
        monitor = EventsMonitor(ExternalProcess(pid=PID), recorder, StreamingStats())
        monitor._ingest(PID, batch)

        assert half_written.ts_start > half_written.ts_stop
        assert in_flight.collections not in {record.collections for record in recorder.records}

    def test_the_copy_made_ahead_of_a_write_is_not_counted_twice(self) -> None:
        """Between the memcpy and the `collections` increment, two slots hold
        the same record. Nothing tells them apart by threshold, so `_ingest`
        keys the run on the counter."""
        truth = capture_records()
        settled = truth[0][20]
        batch = self.batch_at(settled.ts_stop)
        twin_slot = (settled.collections + 1) % RING_SIZES[0]
        batch[twin_slot] = settled

        recorder = Recorder()
        stats = StreamingStats()
        monitor = EventsMonitor(ExternalProcess(pid=PID), recorder, stats)
        monitor._ingest(PID, batch)

        counters = [record.collections for record in recorder.records if record.gen == 0]
        assert counters == sorted(set(counters))
        assert settled.collections in counters
        assert stats.lost_count(PID, 0) == 0

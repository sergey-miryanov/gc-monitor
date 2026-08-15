import logging
from collections import deque
from collections.abc import Iterator, Sequence, Set
from itertools import chain
from typing import Protocol

import msgspec

try:
    from ddsketch import DDSketch

    HAS_DDSKETCH = True
except ImportError:
    HAS_DDSKETCH = False

from .data import secs_to_ns
from .protocol import (
    TGCStatsInfo,
    has_clear_weakrefs,
    has_deduce_unreachable,
    has_delete_garbage,
    has_finalize_garbage,
    has_handle_resurrected,
    has_handle_weakrefs,
    has_incremental,
    has_mark_alive,
    has_pause_ts,
)

logger = logging.getLogger(__name__)


def get_quantile_value(buffer: Sequence[float], q: int) -> float:
    if not buffer:
        return 0.0

    idx = (q / 100.0) * (len(buffer) - 1)
    lower = int(idx)
    upper = lower + 1
    if upper >= len(buffer):
        return buffer[-1]
    weight = idx - lower
    return buffer[lower] * (1 - weight) + buffer[upper] * weight


class Stats:
    MAX_BUFFER_LEN = 1024
    REL_ACCURACY = 0.001

    def __init__(self) -> None:
        self._sketch: DDSketch | None = None
        if HAS_DDSKETCH:
            self._sketch = DDSketch(relative_accuracy=self.REL_ACCURACY)
        self._data: deque[float] = deque(maxlen=self.MAX_BUFFER_LEN)
        self._sum: float = 0
        self._count: int = 0
        self._percentiles: dict[int, float] | None = None

    def update(self, value: float) -> None:
        if self._percentiles is not None:
            raise RuntimeError("Cannot update Stats after materialize() has been called")

        if self._sketch is not None:
            self._sketch.add(value)

        self._data.append(value)

        self._sum += value
        self._count += 1

    def materialize(self) -> None:
        """Settle the percentiles and give the buffer back.

        Final. The caller settles a ring once its process has exited, so no
        value can arrive afterwards, and the four percentiles left behind cover
        every value this instance saw.
        """
        if self._percentiles is not None or self._count == 0:
            return
        sorted_data = sorted(self._data)
        self._percentiles = {
            50: get_quantile_value(sorted_data, 50),
            90: get_quantile_value(sorted_data, 90),
            95: get_quantile_value(sorted_data, 95),
            99: get_quantile_value(sorted_data, 99),
        }
        self._data.clear()
        self._sketch = None

    def average(self) -> float:
        if self._count == 0:
            return 0.0
        return self._sum / self._count

    def percentile(self, p: int) -> float:
        if not 0 <= p <= 100:
            raise ValueError(f"percentile must be in [0, 100], got {p}")
        if self._percentiles is not None:
            return self._percentiles.get(p, 0.0)
        if self._sketch is not None and self._count >= self.MAX_BUFFER_LEN:
            q = self._sketch.get_quantile_value(p / 100.0)
            if q is not None:
                return q
        return get_quantile_value(sorted(self._data), p)

    def count(self) -> int:
        return self._count

    def sum(self) -> float:
        return self._sum

    @property
    def buffer(self) -> Sequence[float]:
        return self._data

    @property
    def has_sketch(self) -> bool:
        return self._sketch is not None

    @property
    def percentiles(self) -> dict[int, float] | None:
        return self._percentiles


class Metric(Protocol):
    name: str

    def get_values(self, item: object) -> tuple[int, int]: ...


class PauseMetric:
    def __init__(self) -> None:
        self.name = "GC Pause"

    def get_values(self, item: object) -> tuple[int, int]:
        if has_pause_ts(item):
            return item.ts_start, item.ts_stop
        return 0, 0


class MarkAliveMetric:
    def __init__(self) -> None:
        self.name = "GC Mark Alive"

    def get_values(self, item: object) -> tuple[int, int]:
        if has_mark_alive(item):
            return item.ts_mark_alive_start, item.ts_mark_alive_stop
        return 0, 0


class FillIncrementMetric:
    def __init__(self) -> None:
        self.name = "GC Fill Increment"

    def get_values(self, item: object) -> tuple[int, int]:
        if has_incremental(item):
            return item.ts_fill_increment_start, item.ts_fill_increment_stop
        return 0, 0


class DeduceUnreachableMetric:
    def __init__(self) -> None:
        self.name = "GC Deduce Unreachable"

    def get_values(self, item: object) -> tuple[int, int]:
        if has_deduce_unreachable(item):
            return item.ts_deduce_unreachable_start, item.ts_deduce_unreachable_stop
        return 0, 0


class HandleWeakrefsMetric:
    def __init__(self) -> None:
        self.name = "GC Handle Weakrefs Callbacks"

    def get_values(self, item: object) -> tuple[int, int]:
        if has_handle_weakrefs(item):
            return item.ts_handle_weakref_callbacks_start, item.ts_handle_weakref_callbacks_stop
        return 0, 0


class FinalizeGarbageMetric:
    def __init__(self) -> None:
        self.name = "GC Finalize Garbage"

    def get_values(self, item: object) -> tuple[int, int]:
        if has_finalize_garbage(item):
            return item.ts_handle_weakref_callbacks_stop, item.ts_finalize_garbage_stop
        return 0, 0


class HandleResurrectedMetric:
    def __init__(self) -> None:
        self.name = "GC Handle Resurrected"

    def get_values(self, item: object) -> tuple[int, int]:
        if has_handle_resurrected(item):
            return item.ts_finalize_garbage_stop, item.ts_handle_resurrected_stop
        return 0, 0


class ClearWeakrefsMetric:
    def __init__(self) -> None:
        self.name = "GC Clear Weakrefs"

    def get_values(self, item: object) -> tuple[int, int]:
        if has_clear_weakrefs(item):
            return item.ts_handle_resurrected_stop, item.ts_clear_weakrefs_stop
        return 0, 0


class DeleteGarbageMetric:
    def __init__(self) -> None:
        self.name = "GC Delete Garbage"

    def get_values(self, item: object) -> tuple[int, int]:
        if has_delete_garbage(item):
            return item.ts_delete_garbage_start, item.ts_delete_garbage_stop
        return 0, 0


METRICS: dict[str, Metric] = {
    "pause": PauseMetric(),
    "mark_alive": MarkAliveMetric(),
    "fill_increment": FillIncrementMetric(),
    "deduce_unreachable": DeduceUnreachableMetric(),
    "handle_weakrefs": HandleWeakrefsMetric(),
    "finalize_garbage": FinalizeGarbageMetric(),
    "handle_resurrected": HandleResurrectedMetric(),
    "clear_weakrefs": ClearWeakrefsMetric(),
    "delete_garbage": DeleteGarbageMetric(),
}


TStatsData = dict[str, dict[int, Stats]]

# (pid, iid). One interpreter's sampled metrics, a generation dict per
# metric. One ring's durations are one of those generations. This is the key
# of a ring gcmon is reading now, since only one process holds a pid at a time.
type RingKey = tuple[int, int]

# (pid, iid, index). `index` counts the processes that have held the pid, from
# 1, and advances when gcmon sees one exit. Everything a run keeps to the end
# is keyed this way, so a successor's figures never land on its predecessor's.
type IndexedRing = tuple[int, int, int]


class LossTotals(msgspec.Struct):
    """Records gcmon never read, and the pause time they held.

    `StreamingStats` accumulates into one of these per key and hands readers
    a `PauseTotals` instead.
    """

    count: int = 0
    pause_ns: int = 0

    def add(self, count: int, pause_ns: int) -> None:
        self.count += count
        self.pause_ns += pause_ns


class PauseTotals(msgspec.Struct, frozen=True, gc=False):
    """One generation's pauses, for one pid or for all of them.

    `sampled_*` is what gcmon measured, `lost_*` what the target's counters
    say it missed. ADR-0015 covers why adding them is exact.

    Frozen because both reads build one from four scalars. A write to what
    you got back would land on a snapshot gcmon never reads again. Those
    scalars cannot hold a cycle either, so the collector need not track one.
    """

    sampled_count: int = 0
    sampled_pause_ns: float = 0.0
    lost_count: int = 0
    lost_pause_ns: int = 0

    @property
    def exact_count(self) -> int:
        """Collections gcmon accounts for, seen and unseen alike."""
        return self.sampled_count + self.lost_count

    @property
    def exact_pause_ns(self) -> float:
        """Pause time over those same collections: sampled plus lost."""
        return self.sampled_pause_ns + self.lost_pause_ns

    @property
    def coverage(self) -> float:
        """Observed share of those collections, in ``[0, 1]``.

        An empty generation reports 1.0, so no call site needs a guard.
        """
        # Summed here rather than read off `exact_count`, which costs a
        # property call to do the same addition.
        exact = self.sampled_count + self.lost_count
        if exact == 0:
            return 1.0
        return self.sampled_count / exact

    @property
    def scale_factor(self) -> float:
        """Multiplier taking a sampled pause sum to the exact one.

        Sub-phases have no exact counterpart but partition the pause, so
        scaling a measured phase sum estimates it. It cannot correct a
        percentile (ADR-0015).
        """
        sampled = self.sampled_pause_ns
        if sampled == 0:
            return 1.0
        return (sampled + self.lost_pause_ns) / sampled


class LifetimeTotals(msgspec.Struct):
    """One ring's own cumulative counters, as the target keeps them.

    A poll overwrites the slot, and a fold sums slots into a fresh one. Both
    reads return that fresh one, never a slot, so this side needs no
    freezing.
    """

    collections: int = 0
    duration_s: float = 0.0

    def add(self, collections: int, duration_s: float) -> None:
        self.collections += collections
        self.duration_s += duration_s

    @property
    def pause_ns(self) -> int:
        """The same history in nanoseconds.

        The target counts seconds here and nanoseconds everywhere else.
        """
        return secs_to_ns(self.duration_s)


class RingStats(msgspec.Struct):
    """Everything one interpreter of one process accumulates.

    One entry per key, so a ring's three kinds of number settle together on
    the exit that ends them and travel together into the report.

    `metrics` is ``None`` until the ring is admitted, and stays ``None`` where
    the bound declined it. Sample buffers are what the bound caps, at a
    thousand values a generation a metric, while the two counter dicts beside
    them hold four numbers a generation. Every ring keeps those whatever the
    table can hold, so `Total` and the footer stay whole.

    `declined` is what tells the two apart, and it needs no scoping of its
    own: an exit settles this entry and gives whatever claims the pid next a
    fresh one, so a decline lasts exactly as long as the process it was made
    against.
    """

    metrics: TStatsData | None = None
    declined: bool = False
    loss: dict[int, LossTotals] = msgspec.field(default_factory=dict)
    lifetime: dict[int, LifetimeTotals] = msgspec.field(default_factory=dict)

    def settle(self) -> None:
        """Fix the percentiles and give the sample buffers back."""
        if self.metrics is None:
            return
        for phase_stats in self.metrics.values():
            for stats in phase_stats.values():
                stats.materialize()

    def sampled(self, gen: int) -> Stats:
        """The pause durations gcmon read for one generation of this ring."""
        if self.metrics is None:
            return Stats()
        return self.metrics["pause"][gen]

    def pause_totals(self, gen: int) -> PauseTotals:
        """One generation, sampled and lost together."""
        sampled = self.sampled(gen)
        lost = self.loss.get(gen, LossTotals())
        return PauseTotals(sampled.count(), sampled.sum(), lost.count, lost.pause_ns)


def _record(stats: TStatsData, item: TGCStatsInfo, metric_name: str) -> None:
    """Record a phase duration in nanoseconds, the unit every metric keeps."""
    metric = METRICS[metric_name]
    ts_start, ts_stop = metric.get_values(item)
    gen = item.gen

    if ts_start != ts_stop:
        stats[metric_name][gen].update(ts_stop - ts_start)


class StreamingStats:
    # Counted over the interpreters running with sample buffers, one set per
    # (pid, iid) covering that interpreter's three generations. A set costs
    # what it did when the bound was 64 processes, so 256 gives each of those
    # processes four interpreters. A process that exits settles its buffers
    # and hands the slots back.
    MAX_ACTIVE_RINGS = 256
    GENS = (0, 1, 2)
    # Under this, the sampled percentiles cover too little of the run to leave
    # a reader working it out from `Cov`, so gcmon says so once.
    COVERAGE_ADVISORY = 0.9

    def __init__(self) -> None:
        self._count: int = 0
        # Phase durations in nanoseconds, per metric and generation.
        self.metrics: TStatsData = {metric: {gen: Stats() for gen in self.GENS} for metric in METRICS}
        # The rings of the processes running now. An entry leaves on the exit
        # that settles it.
        self._metrics_per_ring: dict[RingKey, RingStats] = {}
        # The rings of the processes that have exited, settled and kept for
        # the report. Nothing reopens one, so no successor of a reused pid can
        # add to what its predecessor earned.
        self._materialized_metrics: dict[IndexedRing, RingStats] = {}
        # Running rings holding sample buffers, which is what the bound counts.
        # A ring with only its counters costs too little to bound.
        self._admitted_rings = 0
        # Which process holds each pid, counting from 1.
        self._index_per_pid: dict[int, int] = {}
        # The pids gcmon has records from and has not seen exit.
        self._open_pids: set[int] = set()
        self._bound_warned = False
        # Process-wide, with no generation and no interpreter affinity
        # (ADR-0004), so the high-water mark stays keyed on the process. Two
        # processes that shared a pid keep a mark each.
        self._heap_size: dict[tuple[int, int], int] = {}
        self._read_time: Stats = Stats()

    def update(self, pid: int, item: TGCStatsInfo) -> None:
        self._count += 1

        for metric in METRICS:
            _record(self.metrics, item, metric)

        index = self._index(pid)
        # Process-wide and one integer per process, so it is kept whether or
        # not the ring behind the record has a row.
        self._heap_size[(pid, index)] = max(self._heap_size.get((pid, index), 0), item.heap_size)

        ring = self._ring(pid, item.iid)
        metrics = ring.metrics or self._admit(ring, (pid, item.iid))
        if metrics is None:
            return

        for metric in METRICS:
            _record(metrics, item, metric)

    def _ring(self, pid: int, iid: int) -> RingStats:
        """The entry the records arriving now belong to, opened if new.

        Every ring gets one, since loss and lifetime totals are due from a
        ring the bound turned away as much as from one it admitted.
        """
        key = (pid, iid)
        ring = self._metrics_per_ring.get(key)
        if ring is None:
            ring = RingStats()
            self._metrics_per_ring[key] = ring
        return ring

    def _index(self, pid: int) -> int:
        """Which process holding *pid* the records arriving now belong to.

        Counts from 1 and advances on the exit gcmon sees, so a successor
        files everything apart from its predecessor. Reading it is what marks
        the pid as running, since records only come from a process that is.
        """
        self._open_pids.add(pid)
        return self._index_per_pid.setdefault(pid, 1)

    def _latest_index(self, pid: int) -> int:
        """Which process a reader naming no index means: the one running, or
        the last one that ran."""
        index = self._index_per_pid.get(pid, 1)
        return index if pid in self._open_pids else index - 1

    def _admit(self, ring: RingStats, key: RingKey) -> TStatsData | None:
        """Give *ring* its sample buffers, or ``None`` where none are free.

        A ring gets them on its first record and keeps them until its process
        exits. ``None`` means the record counts towards `Total` and the ring's
        own counters, and reaches no row, which happens when all 256 slots
        were taken by running interpreters at the moment this ring appeared.

        Either way every printed row describes one process's ring over one
        unbroken stretch, so a row's `Count` and its percentiles always cover
        the same records.
        """
        if ring.declined:
            # Declined once, declined for as long as this entry stands. A slot
            # freed by another process's exit would otherwise open a row
            # covering the tail of a ring's life, with nothing on it marking
            # where it starts.
            return None

        if self._admitted_rings >= self.MAX_ACTIVE_RINGS:
            self._decline(ring, key)
            return None

        ring.metrics = {metric: {gen: Stats() for gen in self.GENS} for metric in METRICS}
        self._admitted_rings += 1
        return ring.metrics

    def _decline(self, ring: RingStats, key: RingKey) -> None:
        """Note that this ring gets no row, saying why the first time."""
        ring.declined = True
        if self._bound_warned:
            return

        self._bound_warned = True
        logger.warning(
            "PID %s interpreter %s: gcmon already holds detailed statistics for %s running "
            "interpreters, the most it keeps at once. Records read from any further interpreter are "
            "counted in the run totals, and gcmon prints no row of its own for it.",
            *key,
            self.MAX_ACTIVE_RINGS,
        )

    def materialize(self, pid: int) -> None:
        """Settle every ring of *pid*, which has exited, and advance its index.

        Final, and that is what makes it right: a process that exited sends no
        more records, so each percentile settled here covers its ring end to
        end. The sample buffers go back to the run, and so do the slots, so a
        target that spawns and exits keeps its rows without exhausting the
        bound.

        Whatever claims the pid next reads the advanced index and starts
        clean, with a row and a set of totals of its own.
        """
        if pid not in self._open_pids:
            return

        index = self._index_per_pid.get(pid, 1)
        self._open_pids.discard(pid)
        self._index_per_pid[pid] = index + 1

        for key in [ring for ring in self._metrics_per_ring if ring[0] == pid]:
            settled = self._metrics_per_ring.pop(key)
            if settled.metrics is not None:
                self._admitted_rings -= 1
            settled.settle()
            self._materialized_metrics[(*key, index)] = settled

    def retain(self, pids: Set[int]) -> None:
        """Settle every ring whose process is not in *pids*.

        The caller polls the target's children each tick, so a pid missing
        from that listing has gone.
        """
        for pid in self._open_pids - set(pids):
            self.materialize(pid)

    def record_read_time(self, duration_ns: int) -> None:
        self._read_time.update(duration_ns)

    def record_loss(self, pid: int, iid: int, gen: int, lost_count: int, lost_pause_ns: int) -> None:
        """Record one interval's worth of records gcmon did not read.

        `record_loss` hands over one poll's gap at a time, so these sum.
        Sampled plus lost is the exact total ADR-0015 defines, so the rings
        themselves stay in the monitor.
        """
        self._index(pid)
        ring = self._ring(pid, iid)
        ring.loss.setdefault(gen, LossTotals()).add(lost_count, lost_pause_ns)

    def low_coverage(self, pid: int) -> tuple[int, int, float] | None:
        """The least covered ring of *pid* when it sits under
        `COVERAGE_ADVISORY`, as its interpreter, its generation and its
        coverage. ``None`` on a healthy run.

        Idempotent: the caller owns the warn-once latch and the wording.

        The caller says it once, so a marginal 87% must not stand for a
        capture holding an interpreter at 5%. The latch keeps whichever
        answer came first in time, so a ring that collapses after the warning
        fires goes unnamed.

        Every poll of every pid asks, so it reads the two counts coverage
        needs rather than building a `PauseTotals`. Loss leads: a ring that
        lost nothing cannot be under-covered.

        Only the rings running now, which are the pid's, since the caller
        polled it.
        """
        worst: tuple[int, int, float] | None = None
        for (ring_pid, iid), ring in self._metrics_per_ring.items():
            if ring_pid != pid or ring.declined:
                # A declined ring has a sampled count of zero here, which would
                # read as nothing observed. gcmon read its records and counted
                # them in `Total`, so the advisory has nothing to say about it.
                continue
            for gen, lost in ring.loss.items():
                if not lost.count:
                    continue
                # Something was lost, so the denominator cannot be zero.
                sampled = ring.sampled(gen).count()
                coverage = sampled / (sampled + lost.count)
                if coverage < self.COVERAGE_ADVISORY and (worst is None or coverage < worst[2]):
                    worst = (iid, gen, coverage)
        return worst

    def record_lifetime(self, pid: int, iid: int, gen: int, collections: int, duration_s: float) -> None:
        """Record one ring's totals since its interpreter started.

        The target counts both of them cumulatively, so the newest values
        replace the previous ones. A successor on a reused pid writes into an
        entry of its own, so the fold adds the two rather than losing the
        larger history to the smaller one that follows it.
        """
        self._index(pid)
        self._ring(pid, iid).lifetime[gen] = LifetimeTotals(collections, duration_s)

    def pause_totals(self, pid: int, iid: int, gen: int, index: int | None = None) -> PauseTotals:
        """One ring, read once.

        *index* names which process held the pid; left out, it reads the one
        running now or the last one that ran. Every ring at once is
        :meth:`pause_totals_by_gen`, which costs a pass instead.
        """
        ring = self._entry(pid, iid, index)
        if ring is None:
            return PauseTotals()
        return ring.pause_totals(gen)

    def _entries(self) -> Iterator[RingStats]:
        """Every ring of the run, running and settled alike."""
        return chain(self._metrics_per_ring.values(), self._materialized_metrics.values())

    def pause_totals_by_gen(self) -> dict[int, PauseTotals]:
        """Every generation's pause totals over every ring."""
        # Folded here rather than behind a helper, which had this one caller.
        lost: dict[int, LossTotals] = {}
        for ring in self._entries():
            for gen, loss in ring.loss.items():
                lost.setdefault(gen, LossTotals()).add(loss.count, loss.pause_ns)

        pause = self.metrics["pause"]
        by_gen = {}
        for gen in self.GENS:
            sampled = pause[gen]
            gen_lost = lost.get(gen)
            by_gen[gen] = PauseTotals(
                sampled.count(),
                sampled.sum(),
                gen_lost.count if gen_lost is not None else 0,
                gen_lost.pause_ns if gen_lost is not None else 0,
            )
        return by_gen

    def lifetime_scope(self) -> tuple[int, int]:
        """How many interpreters, in how many processes, the lifetime fold
        covers.

        The footnote states both, so a reader can tell one interpreter's
        history from a sum over five that started at different moments.

        Two processes that shared a pid count as two, since the index tells
        them apart. A pid gcmon never saw exit still counts as one.
        """
        interpreters = {
            key for key, ring in self._keyed_entries() if any(totals.collections for totals in ring.lifetime.values())
        }
        return len(interpreters), len({(pid, index) for pid, _iid, index in interpreters})

    def lifetime_totals_by_gen(self) -> dict[int, LifetimeTotals]:
        """Fold every ring's lifetime totals into a per-gen one, single pass."""
        by_gen: dict[int, LifetimeTotals] = {}
        for ring in self._entries():
            for gen, totals in ring.lifetime.items():
                by_gen.setdefault(gen, LifetimeTotals()).add(totals.collections, totals.duration_s)
        return by_gen

    @property
    def read_time(self) -> Stats:
        """Read durations in nanoseconds, over every polled pid."""
        return self._read_time

    def _entry(self, pid: int, iid: int, index: int | None = None) -> RingStats | None:
        """One ring, running or settled.

        *index* names which process held the pid, counting from 1. Left out,
        it reads the one running now or the last one that ran.
        """
        if index is None:
            index = self._latest_index(pid)
        if pid in self._open_pids and index == self._index_per_pid.get(pid, 1):
            return self._metrics_per_ring.get((pid, iid))
        return self._materialized_metrics.get((pid, iid, index))

    def _keyed_entries(self) -> Iterator[tuple[IndexedRing, RingStats]]:
        """Every ring of the run under the key the report names it by."""
        for (pid, iid), ring in self._metrics_per_ring.items():
            yield (pid, iid, self._index_per_pid.get(pid, 1)), ring
        yield from self._materialized_metrics.items()

    def get_ring_stats(self, pid: int, iid: int, index: int | None = None) -> TStatsData | None:
        """One interpreter's sampled metrics, still filling or settled.

        ``None`` where the ring has none, which is a key gcmon never read or a
        ring the bound declined.
        """
        ring = self._entry(pid, iid, index)
        return ring.metrics if ring is not None else None

    def rings(self) -> list[IndexedRing]:
        """Every ring with a row, in the order the table prints them.

        One entry per process that held the pid, so a reused pid brings a row
        for each. A ring the bound declined has no sampled metrics and no row;
        :meth:`untracked_rings` counts those.
        """
        return sorted(key for key, ring in self._keyed_entries() if ring.metrics is not None)

    def untracked_rings(self) -> int:
        """How many rings reached `update` with no slot to take.

        Their records are in `Total` and in the coverage figures, so the
        footer states the count rather than leaving a reader to add the rows
        up and find them short.
        """
        return sum(1 for ring in self._entries() if ring.declined)

    def count(self) -> int:
        return self._count

    def heap_size_p99(self) -> float | None:
        """The 99th percentile of the per-process high-water heap sizes.

        ``None`` when no record carried one, so a caller leaves the metric
        out rather than publishing a zero.
        """
        sizes = self._heap_size.values()
        if not sizes:
            return None
        if len(sizes) == 1:
            # Every percentile of one mark is that mark, and one monitored pid
            # is the usual case.
            return float(next(iter(sizes)))
        return get_quantile_value(sorted(sizes), 99)

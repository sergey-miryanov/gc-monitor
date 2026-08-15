import logging
from collections import deque
from collections.abc import Sequence, Set
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
# metric. One ring's durations are one of those generations.
type RingKey = tuple[int, int]

# (pid, iid, gen). `record_loss` delivers increments, and a fold over them
# waits for a read, so two interpreters of one pid stay apart.
type LossKey = tuple[int, int, int]

# (pid, iid, gen). Lifetime totals are cumulative and overwrite each other, so
# every interpreter keeps a slot of its own and the summing waits for a read.
type LifetimeKey = tuple[int, int, int]


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


def _record(stats: TStatsData, item: TGCStatsInfo, metric_name: str) -> None:
    """Record a phase duration in nanoseconds, the unit every metric keeps."""
    metric = METRICS[metric_name]
    ts_start, ts_stop = metric.get_values(item)
    gen = item.gen

    if ts_start != ts_stop:
        stats[metric_name][gen].update(ts_stop - ts_start)


class StreamingStats:
    # Counted over the interpreters still running, one entry per (pid, iid)
    # holding that interpreter's three generations. An entry costs what it did
    # when the bound was 64 processes, so 256 gives each of those processes
    # four interpreters. A process that exits settles its entries and hands
    # the slots back.
    MAX_ACTIVE_RINGS = 256
    GENS = (0, 1, 2)
    # Under this, the sampled percentiles cover too little of the run to leave
    # a reader working it out from `Cov`, so gcmon says so once.
    COVERAGE_ADVISORY = 0.9

    def __init__(self) -> None:
        self._count: int = 0
        # Phase durations in nanoseconds, per metric and generation.
        self.metrics: TStatsData = {metric: {gen: Stats() for gen in self.GENS} for metric in METRICS}
        # Every ring with a row, whether it is still filling or settled. A key
        # here belongs to one process for the whole run: nothing reopens a
        # settled entry, so no successor of a reused pid can add to it.
        self._metrics_per_ring: dict[RingKey, TStatsData] = {}
        # The subset still taking records, which is what the bound counts.
        self._live_rings: set[RingKey] = set()
        # Rings that reached `update` with no slot to take. Their records
        # reach `Total`, and the footer says how many rings have no row.
        self._untracked_rings: set[RingKey] = set()
        self._bound_warned = False
        self._reuse_warned = False
        # Process-wide, with no generation and no interpreter affinity
        # (ADR-0004), so the high-water mark stays keyed on the pid.
        self._heap_size: dict[int, int] = {}
        self._read_time: Stats = Stats()
        # `record_loss` hands over one poll's gap at a time, so these sum.
        # Sampled plus lost is the exact total ADR-0015 defines, so the rings
        # themselves stay in the monitor. Nothing prunes a dead pid.
        self._loss: dict[LossKey, LossTotals] = {}
        self._lifetime: dict[LifetimeKey, LifetimeTotals] = {}

    def update(self, pid: int, item: TGCStatsInfo) -> None:
        self._count += 1

        for metric in METRICS:
            _record(self.metrics, item, metric)

        # Process-wide and one integer per pid, so it is kept whether or not
        # the ring behind the record has a row.
        self._heap_size[pid] = max(self._heap_size.get(pid, 0), item.heap_size)

        ring = self._admit((pid, item.iid))
        if ring is None:
            return

        for metric in METRICS:
            _record(ring, item, metric)

    def _admit(self, key: RingKey) -> TStatsData | None:
        """The entry taking *key*'s records, or ``None`` when it has none.

        A ring opens its entry on its first record and keeps it for the run.
        ``None`` means the record counts towards `Total` and nothing else,
        which happens two ways: every slot was taken when the ring first
        appeared, or the pid was reused and its predecessor holds the entry.

        Both leave every printed row describing one process's ring over one
        unbroken stretch, so a row's `Count` and its percentiles always cover
        the same records.
        """
        if key in self._live_rings:
            return self._metrics_per_ring[key]

        if key in self._untracked_rings:
            # Declined once, declined for the run. A slot freed by an exit
            # would otherwise open a row covering the tail of a ring's life,
            # with nothing on it marking where it starts.
            return None

        if key in self._metrics_per_ring:
            self._decline_reused(key)
            return None

        if len(self._live_rings) >= self.MAX_ACTIVE_RINGS:
            self._decline_over_bound(key)
            return None

        ring: TStatsData = {metric: {gen: Stats() for gen in self.GENS} for metric in METRICS}
        self._metrics_per_ring[key] = ring
        self._live_rings.add(key)
        return ring

    def _decline_reused(self, key: RingKey) -> None:
        """A settled entry holds this key, so the process using the pid now
        gets no row of its own."""
        self._untracked_rings.add(key)
        if self._reuse_warned:
            return

        self._reuse_warned = True
        logger.warning(
            "PID %s interpreter %s: this pid ran before, and the statistics under it describe the "
            "process that exited. Records read from the process holding it now are counted in the "
            "run totals, and gcmon prints no row of its own for it.",
            *key,
        )

    def _decline_over_bound(self, key: RingKey) -> None:
        """The bound was full of running interpreters when this ring arrived."""
        self._untracked_rings.add(key)
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
        """Settle every ring of *pid*, which has exited.

        Final, and that is what makes it right: a process that exited sends no
        more records, so each percentile settled here covers its ring end to
        end. The sample buffers go back to the run, and so do the slots, so a
        target that spawns and exits keeps its rows without exhausting the
        bound.
        """
        for key in [live for live in self._live_rings if live[0] == pid]:
            self._live_rings.discard(key)
            for phase_stats in self._metrics_per_ring[key].values():
                for stats in phase_stats.values():
                    stats.materialize()

    def retain(self, pids: Set[int]) -> None:
        """Settle every ring whose process is not in *pids*.

        The caller polls the target's children each tick, so a pid missing
        from that listing has gone.
        """
        for pid in {key[0] for key in self._live_rings} - set(pids):
            self.materialize(pid)

    def record_read_time(self, duration_ns: int) -> None:
        self._read_time.update(duration_ns)

    def record_loss(self, pid: int, iid: int, gen: int, lost_count: int, lost_pause_ns: int) -> None:
        """Record one interval's worth of records gcmon did not read."""
        self._loss.setdefault((pid, iid, gen), LossTotals()).add(lost_count, lost_pause_ns)

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
        """
        worst: tuple[int, int, float] | None = None
        for (loss_pid, iid, gen), lost in self._loss.items():
            if loss_pid != pid or not lost.count:
                continue
            if (pid, iid) in self._untracked_rings:
                # A declined ring has a sampled count of zero here, which would
                # read as nothing observed. gcmon read its records and counted
                # them in `Total`, so the advisory has nothing to say about it.
                continue
            # Something was lost, so the denominator cannot be zero.
            sampled = self._sampled(pid, iid, gen).count()
            coverage = sampled / (sampled + lost.count)
            if coverage < self.COVERAGE_ADVISORY and (worst is None or coverage < worst[2]):
                worst = (iid, gen, coverage)
        return worst

    def record_lifetime(self, pid: int, iid: int, gen: int, collections: int, duration_s: float) -> None:
        """Record one ring's totals since its interpreter started.

        The target counts both of them cumulatively, so the newest values
        replace the previous ones.
        """
        self._lifetime[(pid, iid, gen)] = LifetimeTotals(collections, duration_s)

    def _sampled(self, pid: int, iid: int, gen: int) -> Stats:
        """The pause durations gcmon sampled for one ring."""
        ring_data = self.get_ring_stats(pid, iid)
        if ring_data is None:
            return Stats()
        return ring_data["pause"][gen]

    def pause_totals(self, pid: int, iid: int, gen: int) -> PauseTotals:
        """One ring, read once.

        Two dict lookups. Every ring at once is
        :meth:`pause_totals_by_gen`, which costs a pass instead.
        """
        sampled = self._sampled(pid, iid, gen)
        lost = self._loss.get((pid, iid, gen), LossTotals())
        return PauseTotals(sampled.count(), sampled.sum(), lost.count, lost.pause_ns)

    def pause_totals_by_gen(self) -> dict[int, PauseTotals]:
        """Every generation's pause totals over every ring."""
        # Folded here rather than behind a helper, which had this one caller,
        # and skipped when nothing was lost.
        lost: dict[int, LossTotals] = {}
        if self._loss:
            for (_pid, _iid, gen), loss in self._loss.items():
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

        The second count is distinct pids. A reused pid understates it: the
        two processes that held it fold into one entry here, as they do in
        the figure beside it (ADR-0016).
        """
        interpreters = {(pid, iid) for (pid, iid, _gen), totals in self._lifetime.items() if totals.collections}
        return len(interpreters), len({pid for pid, _iid in interpreters})

    def lifetime_totals_by_gen(self) -> dict[int, LifetimeTotals]:
        """Fold every ring's lifetime totals into a per-gen one, single pass."""
        by_gen: dict[int, LifetimeTotals] = {}
        if not self._lifetime:
            return by_gen
        for (_pid, _iid, gen), totals in self._lifetime.items():
            by_gen.setdefault(gen, LifetimeTotals()).add(totals.collections, totals.duration_s)
        return by_gen

    @property
    def read_time(self) -> Stats:
        """Read durations in nanoseconds, over every polled pid."""
        return self._read_time

    def get_ring_stats(self, pid: int, iid: int) -> TStatsData | None:
        """One interpreter's sampled metrics, still filling or settled."""
        return self._metrics_per_ring.get((pid, iid))

    def rings(self) -> set[RingKey]:
        """Every ring with a row. :meth:`untracked_rings` counts the rest."""
        return set(self._metrics_per_ring)

    def untracked_rings(self) -> int:
        """How many rings reached `update` with no slot to take.

        Their records are in `Total` and in the coverage figures, so the
        footer states the count rather than leaving a reader to add the rows
        up and find them short.
        """
        return len(self._untracked_rings)

    def count(self) -> int:
        return self._count

    def heap_size_p99(self) -> float | None:
        """The 99th percentile of the per-pid high-water heap sizes.

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

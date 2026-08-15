from collections import OrderedDict, deque
from collections.abc import Sequence
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

# (pid, gen). `record_loss` delivers increments, so two interpreters of one
# pid add into the same slot.
type LossKey = tuple[int, int]

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
    MAX_ACTIVE_PIDS = 64
    GENS = (0, 1, 2)
    # Under this, the sampled percentiles cover too little of the run to leave
    # a reader working it out from `Cov`, so gcmon says so once.
    COVERAGE_ADVISORY = 0.9

    def __init__(self) -> None:
        self._count: int = 0
        # Phase durations in nanoseconds, per metric and generation.
        self.metrics: TStatsData = {metric: {gen: Stats() for gen in self.GENS} for metric in METRICS}
        self._metrics_per_pid: OrderedDict[int, TStatsData] = OrderedDict()
        self._materialized_metrics: dict[int, TStatsData] = {}
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

        if pid not in self._metrics_per_pid:
            if len(self._metrics_per_pid) >= self.MAX_ACTIVE_PIDS:
                old_pid, old_stats = self._metrics_per_pid.popitem(last=False)
                for phase_stats in old_stats.values():
                    for s in phase_stats.values():
                        s.materialize()
                self._materialized_metrics[old_pid] = old_stats

            self._metrics_per_pid[pid] = {m: {gen: Stats() for gen in self.GENS} for m in METRICS}

        self._metrics_per_pid.move_to_end(pid)

        for metric in METRICS:
            _record(self._metrics_per_pid[pid], item, metric)

        self._heap_size[pid] = max(self._heap_size.get(pid, 0), item.heap_size)

    def record_read_time(self, duration_ns: int) -> None:
        self._read_time.update(duration_ns)

    def record_loss(self, pid: int, gen: int, lost_count: int, lost_pause_ns: int) -> None:
        """Record one interval's worth of records gcmon did not read."""
        self._loss.setdefault((pid, gen), LossTotals()).add(lost_count, lost_pause_ns)

    def low_coverage(self, pid: int) -> tuple[int, float] | None:
        """The first generation of *pid* below `COVERAGE_ADVISORY`, and its
        coverage. ``None`` on a healthy run.

        Idempotent: the caller owns the warn-once latch and the wording.

        Every poll of every pid asks, so it reads the two counts coverage needs
        rather than building a `PauseTotals`. Loss comes first: one lookup, and
        a generation that lost nothing cannot be under-covered.
        """
        for gen in self.GENS:
            lost = self._loss.get((pid, gen))
            if lost is None or not lost.count:
                continue
            # Something was lost, so the denominator cannot be zero.
            sampled = self._sampled(pid, gen).count()
            coverage = sampled / (sampled + lost.count)
            if coverage < self.COVERAGE_ADVISORY:
                return gen, coverage
        return None

    def record_lifetime(self, pid: int, iid: int, gen: int, collections: int, duration_s: float) -> None:
        """Record one ring's totals since its interpreter started.

        The target counts both of them cumulatively, so the newest values
        replace the previous ones.
        """
        self._lifetime[(pid, iid, gen)] = LifetimeTotals(collections, duration_s)

    def _sampled(self, pid: int, gen: int) -> Stats:
        """The pause durations gcmon sampled for one pid's generation."""
        pid_data = self.get_pid_stats(pid)
        if pid_data is None:
            return Stats()
        return pid_data["pause"][gen]

    def pause_totals(self, pid: int, gen: int) -> PauseTotals:
        """One pid's generation, read once.

        Two dict lookups. Every pid at once is
        :meth:`pause_totals_by_gen`, which costs a pass instead.
        """
        sampled = self._sampled(pid, gen)
        lost = self._loss.get((pid, gen), LossTotals())
        return PauseTotals(sampled.count(), sampled.sum(), lost.count, lost.pause_ns)

    def pause_totals_by_gen(self) -> dict[int, PauseTotals]:
        """Every generation's pause totals over every pid."""
        # Folded here rather than behind a helper, which had this one caller,
        # and skipped when nothing was lost.
        lost: dict[int, LossTotals] = {}
        if self._loss:
            for (_pid, gen), loss in self._loss.items():
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

    def get_pid_stats(self, pid: int) -> TStatsData | None:
        return self._metrics_per_pid.get(pid) or self._materialized_metrics.get(pid)

    def pids(self) -> set[int]:
        return set(self._metrics_per_pid) | set(self._materialized_metrics)

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

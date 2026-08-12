import logging
from collections import Counter, OrderedDict, deque
from collections.abc import Sequence
from typing import Protocol

import msgspec

try:
    from ddsketch import DDSketch

    HAS_DDSKETCH = True
except ImportError:
    HAS_DDSKETCH = False

from .data import dur_to_ms, secs_to_ns
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

logger = logging.getLogger("gcmon")

# The interpreter CPython creates at startup, and the last one it tears down.
MAIN_INTERPRETER = 0


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
    """Records gcmon never read, and the pause time they held."""

    count: int = 0
    pause_ns: int = 0

    def add(self, count: int, pause_ns: int) -> None:
        self.count += count
        self.pause_ns += pause_ns


class LifetimeTotals(msgspec.Struct):
    """What a ring collected since its interpreter started, on the target's
    own counters."""

    collections: int = 0
    duration_s: float = 0.0

    def add(self, collections: int, duration_s: float) -> None:
        self.collections += collections
        self.duration_s += duration_s


def _record(stats: TStatsData, item: TGCStatsInfo, metric_name: str) -> None:
    """Record a phase duration in nanoseconds; conversion happens at display time."""
    metric = METRICS[metric_name]
    ts_start, ts_stop = metric.get_values(item)
    gen = item.gen

    if ts_start != ts_stop:
        stats[metric_name][gen].update(ts_stop - ts_start)


class StreamingStats:
    MAX_ACTIVE_PIDS = 64
    GENS = (0, 1, 2)
    # Below this, the sampled percentiles describe so little of the run that
    # a reader should be told once rather than left to infer it from `Cov`.
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
        # Lifetime is a running total, not an increment, so it is stored per
        # ring and overwritten rather than summed across polls.
        self._lifetime: dict[LifetimeKey, LifetimeTotals] = {}

        # Per generation ring buffer geometry
        self._ring_size: dict[int, int] = {}
        self._coverage_warned = False

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
        """Record the time spent reading GC stats from a target process."""
        self._read_time.update(duration_ns)

    def record_ring_geometry(self, events: Sequence[TGCStatsInfo]) -> None:
        """Calculating ring buffer geometry from the data.

        A target runs the monitor's own Python build, so the geometry holds
        for every interpreter of every pid until the run ends.
        A poll that returned nothing leaves the dict empty and the next
        one tries again.
        """
        if self._ring_size:
            return

        # The main interpreter answers for all of them, since it outlives every
        # subinterpreter and shares their geometry.
        self._ring_size = Counter(event.gen for event in events if event.iid == MAIN_INTERPRETER)

    def ring_size(self, gen: int) -> int:
        """Records the *gen* ring holds, or 0 before any poll reported it."""
        return self._ring_size.get(gen, 0)

    def record_loss(self, pid: int, gen: int, lost_count: int, lost_pause_ns: int) -> None:
        """Record one interval's worth of records gcmon did not read."""
        self._loss.setdefault((pid, gen), LossTotals()).add(lost_count, lost_pause_ns)

    def check_coverage_advisory(self, pid: int) -> None:
        """Warn once if *pid* is reading too little of its target to trust.

        Called after a poll has folded both its loss and its records, never
        from `record_loss`. `_ingest` records loss for every key before it
        updates any of them, so a check inside `record_loss` would divide that
        poll's gap into the sample as it stood before the poll. A run whose
        first gap lands early then latches a figure it never revisits: two
        polls of 2 then 100 records with one lost warned "only 67%" of a run
        that ended at 99%.
        """
        if self._coverage_warned:
            return

        for gen in self.GENS:
            if not self.lost_count(pid, gen) or self.coverage(pid, gen) >= self.COVERAGE_ADVISORY:
                continue
            self._coverage_warned = True
            size = self.ring_size(gen)
            logger.warning(
                "PID %s generation %s: only %.0f%% of collections observed. The ring buffer CPython "
                "exports holds %s record%s, so a target that runs collections more often than gcmon "
                "polls overwrites records before they can be read. Counts and sums below are "
                "reconstructed and exact; percentiles cover only what was sampled and read high.",
                pid,
                gen,
                self.coverage(pid, gen) * 100,
                size,
                "" if size == 1 else "s",
            )
            return

    def record_lifetime(self, pid: int, iid: int, gen: int, collections: int, duration_s: float) -> None:
        """Record one ring's totals since its interpreter started.

        Both fields are cumulative in the target, so the newest values replace
        the previous ones rather than adding to them.
        """
        self._lifetime[(pid, iid, gen)] = LifetimeTotals(collections, duration_s)

    def _sampled(self, pid: int | None, gen: int) -> Stats:
        """The pause durations gcmon sampled, for one pid or all of them."""
        if pid is None:
            return self.metrics["pause"][gen]
        pid_data = self.get_pid_stats(pid)
        if pid_data is None:
            return Stats()
        return pid_data["pause"][gen]

    def _lost(self, pid: int | None, gen: int) -> LossTotals:
        """One pid's generation, or that generation over every pid."""
        if pid is not None:
            return self._loss.get((pid, gen), LossTotals())

        total = LossTotals()
        for (_pid, key_gen), loss in self._loss.items():
            if key_gen == gen:
                total.add(loss.count, loss.pause_ns)
        return total

    def lost_count(self, pid: int | None, gen: int) -> int:
        return self._lost(pid, gen).count

    def lost_pause_ns(self, pid: int | None, gen: int) -> int:
        return self._lost(pid, gen).pause_ns

    def exact_count(self, pid: int | None, gen: int) -> int:
        """Collections over the observed span, seen and unseen alike."""
        return self._sampled(pid, gen).count() + self.lost_count(pid, gen)

    def exact_pause_ns(self, pid: int | None, gen: int) -> float:
        """Pause time over the same span: sampled plus lost, per ADR-0015."""
        return self._sampled(pid, gen).sum() + self.lost_pause_ns(pid, gen)

    def coverage(self, pid: int | None, gen: int) -> float:
        """Observed share of the span, in ``[0, 1]``.

        1.0 when nothing was observed: none of the nothing it covers was lost,
        and every call site would otherwise guard a division.
        """
        exact = self.exact_count(pid, gen)
        if exact == 0:
            return 1.0
        return self._sampled(pid, gen).count() / exact

    def scale_factor(self, pid: int | None, gen: int) -> float:
        """Multiplier taking a sampled pause sum to the exact one.

        Sub-phases have no exact counterpart but partition the pause, so
        scaling a measured phase sum by this estimates it. Percentiles it
        cannot correct; see ADR-0015.
        """
        sampled = self._sampled(pid, gen).sum()
        if sampled == 0:
            return 1.0
        return self.exact_pause_ns(pid, gen) / sampled

    def _lost_by_gen(self) -> dict[int, LossTotals]:
        """Fold every pid's totals into a per-gen one in a single pass."""
        by_gen: dict[int, LossTotals] = {}
        for (_pid, gen), loss in self._loss.items():
            by_gen.setdefault(gen, LossTotals()).add(loss.count, loss.pause_ns)
        return by_gen

    def _lifetime_by_gen(self) -> dict[int, LifetimeTotals]:
        """Every ring's lifetime totals, folded per generation in one pass."""
        by_gen: dict[int, LifetimeTotals] = {}
        for (_pid, _iid, gen), totals in self._lifetime.items():
            by_gen.setdefault(gen, LifetimeTotals()).add(totals.collections, totals.duration_s)
        return by_gen

    def _lifetime_totals(self, pid: int | None, gen: int) -> LifetimeTotals:
        """Summed over the interpreters of *pid*, or of every pid."""
        summed = LifetimeTotals()
        for (key_pid, _iid, key_gen), totals in self._lifetime.items():
            if key_gen == gen and (pid is None or key_pid == pid):
                summed.add(totals.collections, totals.duration_s)
        return summed

    def lifetime_count(self, pid: int | None, gen: int) -> int:
        """Collections since the interpreter started, not since gcmon attached."""
        return self._lifetime_totals(pid, gen).collections

    def lifetime_pause_ns(self, pid: int | None, gen: int) -> int:
        """Pause time over that same whole history."""
        return secs_to_ns(self._lifetime_totals(pid, gen).duration_s)

    @property
    def read_time(self) -> Stats:
        """Read durations in nanoseconds, aggregated over all polled PIDs."""
        return self._read_time

    def get_pid_stats(self, pid: int) -> TStatsData | None:
        return self._metrics_per_pid.get(pid) or self._materialized_metrics.get(pid)

    def pids(self) -> set[int]:
        return set(self._metrics_per_pid) | set(self._materialized_metrics)

    def count(self) -> int:
        return self._count

    def aggregate(self) -> dict[str, int | float]:
        """Summarize pause metrics, with durations converted to milliseconds.

        Sums and counts are exact: what gcmon saw plus what the target's own
        counters say it missed. ``p99`` stays sampled and reads high, since a
        long run delays the next one, so its record survives in the ring more
        often than a short one's. No scale factor corrects a quantile.

        The loss and lifetime totals are folded per generation once, up front:
        going through the per-pid accessors instead would rescan both dicts for
        every field and dominate a call that is otherwise just three quantiles.
        """
        result: dict[str, int | float] = {}
        pause = self.metrics["pause"]
        lost = self._lost_by_gen()
        lifetime = self._lifetime_by_gen()
        exact_total = 0
        for gen in self.GENS:
            s = pause[gen]
            gen_lost = lost.get(gen, LossTotals())
            sampled_count = s.count()
            exact_count = sampled_count + gen_lost.count
            exact_total += exact_count
            if sampled_count > 0:
                result[f"pause_gen_{gen}_p99"] = dur_to_ms(s.percentile(99))
                result[f"pause_gen_{gen}_sum"] = dur_to_ms(s.sum() + gen_lost.pause_ns)
                result[f"pause_gen_{gen}_count"] = exact_count
                result[f"pause_gen_{gen}_coverage"] = sampled_count / exact_count
            gen_lifetime = lifetime.get(gen, LifetimeTotals())
            if gen_lifetime.collections > 0:
                result[f"pause_gen_{gen}_lifetime_count"] = gen_lifetime.collections
                result[f"pause_gen_{gen}_lifetime_sum"] = dur_to_ms(secs_to_ns(gen_lifetime.duration_s))
        if self._heap_size:
            sorted_heaps = sorted(self._heap_size.values())
            result["heap_size_p99"] = get_quantile_value(sorted_heaps, 99)
        result["pause_count"] = exact_total
        return result

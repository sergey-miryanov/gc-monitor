import logging
from collections import OrderedDict, deque
from collections.abc import Sequence
from typing import Protocol

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


def _record(stats: TStatsData, item: TGCStatsInfo, metric_name: str) -> None:
    """Record a phase duration in nanoseconds; conversion happens at display time."""
    metric = METRICS[metric_name]
    ts_start, ts_stop = metric.get_values(item)
    gen = item.gen

    if ts_start != ts_stop:
        stats[metric_name][gen].update(ts_stop - ts_start)


def _ring_size(gen: int) -> str:
    """How many records the target's ring for *gen* holds, for the advisory.

    `GC_YOUNG_STATS_SIZE` is 11 and `GC_OLD_STATS_SIZE` is 3, and both are 1
    under `Py_GIL_DISABLED`. gcmon does not know which build it is attached
    to, so the free-threaded case is named rather than guessed at.
    """
    return f"{11 if gen == 0 else 3} records, or 1 on a free-threaded build"


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
        # Loss arrives as a per-poll increment, so exact totals follow from
        # ADR-0015's invariant without holding the monitor's cursors: what
        # gcmon saw plus what it missed. A pid dropped by `forget` keeps what
        # it recorded.
        self._lost_count: dict[tuple[int, int], int] = {}
        self._lost_pause_ns: dict[tuple[int, int], int] = {}
        # Beside the loss rather than inside it: the windows counted here were
        # recorded above like any other, and this only says how many of them
        # reached the trace as a span. See `LossWindow.is_drawable`.
        self._undrawable_count: dict[tuple[int, int], int] = {}
        # Lifetime is a running total, not an increment, so it is stored per
        # ring and overwritten rather than summed across polls.
        self._lifetime: dict[tuple[int, int, int], tuple[int, float]] = {}
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

    def record_loss(self, pid: int, gen: int, lost_count: int, lost_pause_ns: int) -> None:
        """Record one interval whose records were overwritten before a poll."""
        key = (pid, gen)
        self._lost_count[key] = self._lost_count.get(key, 0) + lost_count
        self._lost_pause_ns[key] = self._lost_pause_ns.get(key, 0) + lost_pause_ns

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
            logger.warning(
                "PID %s generation %s: only %.0f%% of collections observed. The ring buffer CPython "
                "exports holds %s, so a target that runs collections more often than gcmon "
                "polls overwrites records before they can be read. Counts and sums below are "
                "reconstructed and exact; percentiles cover only what was sampled and read high.",
                pid,
                gen,
                self.coverage(pid, gen) * 100,
                _ring_size(gen),
            )
            return

    def record_undrawable(self, pid: int, gen: int) -> None:
        """Record one loss window whose bounds did not describe an interval.

        The loss it measured has already gone through :meth:`record_loss` and
        counts toward every number here; this tallies only the span nobody
        drew, so the footer can say the trace is one bar short and why. See
        `LossWindow.is_drawable`.
        """
        key = (pid, gen)
        self._undrawable_count[key] = self._undrawable_count.get(key, 0) + 1

    def record_lifetime(self, pid: int, iid: int, gen: int, collections: int, duration_s: float) -> None:
        """Record one ring's totals since its interpreter started.

        Both fields are cumulative in the target, so the newest values replace
        the previous ones rather than adding to them.
        """
        self._lifetime[(pid, iid, gen)] = (collections, duration_s)

    def _sampled(self, pid: int | None, gen: int) -> Stats:
        """The pause durations gcmon sampled, for one pid or all of them."""
        if pid is None:
            return self.metrics["pause"][gen]
        pid_data = self.get_pid_stats(pid)
        if pid_data is None:
            return Stats()
        return pid_data["pause"][gen]

    def _lost(self, totals: dict[tuple[int, int], int], pid: int | None, gen: int) -> int:
        if pid is not None:
            return totals.get((pid, gen), 0)
        return sum(value for (_pid, key_gen), value in totals.items() if key_gen == gen)

    def lost_count(self, pid: int | None, gen: int) -> int:
        return self._lost(self._lost_count, pid, gen)

    def lost_pause_ns(self, pid: int | None, gen: int) -> int:
        return self._lost(self._lost_pause_ns, pid, gen)

    def undrawable_count(self, pid: int | None, gen: int) -> int:
        """Loss windows counted but drawn nowhere, per `record_undrawable`.

        Nothing else reads it: it is a count of spans, not of collections, and
        it must not enter `exact_count`, `coverage` or `scale_factor`, all of
        which already carry the loss these windows measured.
        """
        return self._lost(self._undrawable_count, pid, gen)

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

    def _lost_by_gen(self, totals: dict[tuple[int, int], int]) -> dict[int, int]:
        """Fold a per-(pid, gen) total into a per-gen one in a single pass."""
        by_gen: dict[int, int] = {}
        for (_pid, gen), value in totals.items():
            by_gen[gen] = by_gen.get(gen, 0) + value
        return by_gen

    def _lifetime_by_gen(self) -> dict[int, tuple[int, float]]:
        """Every ring's lifetime totals, folded per generation in one pass."""
        by_gen: dict[int, tuple[int, float]] = {}
        for (_pid, _iid, gen), (collections, duration_s) in self._lifetime.items():
            count, total_s = by_gen.get(gen, (0, 0.0))
            by_gen[gen] = (count + collections, total_s + duration_s)
        return by_gen

    def _lifetime_totals(self, pid: int | None, gen: int) -> tuple[int, float]:
        """Summed over the interpreters of *pid*, or of every pid."""
        count = 0
        duration_s = 0.0
        for (key_pid, _iid, key_gen), (collections, duration) in self._lifetime.items():
            if key_gen == gen and (pid is None or key_pid == pid):
                count += collections
                duration_s += duration
        return count, duration_s

    def lifetime_count(self, pid: int | None, gen: int) -> int:
        """Collections since the interpreter started, not since gcmon attached."""
        return self._lifetime_totals(pid, gen)[0]

    def lifetime_pause_ns(self, pid: int | None, gen: int) -> int:
        """Pause time over that same whole history."""
        return secs_to_ns(self._lifetime_totals(pid, gen)[1])

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
        long collection delays its successors and so survives in the ring more
        often than a short one. No scale factor corrects a quantile.

        The loss and lifetime totals are folded per generation once, up front:
        going through the per-pid accessors instead would rescan both dicts for
        every field and dominate a call that is otherwise just three quantiles.
        """
        result: dict[str, int | float] = {}
        pause = self.metrics["pause"]
        lost_counts = self._lost_by_gen(self._lost_count)
        lost_pause_ns = self._lost_by_gen(self._lost_pause_ns)
        lifetime = self._lifetime_by_gen()
        exact_total = 0
        for gen in self.GENS:
            s = pause[gen]
            sampled_count = s.count()
            exact_count = sampled_count + lost_counts.get(gen, 0)
            exact_total += exact_count
            if sampled_count > 0:
                result[f"pause_gen_{gen}_p99"] = dur_to_ms(s.percentile(99))
                result[f"pause_gen_{gen}_sum"] = dur_to_ms(s.sum() + lost_pause_ns.get(gen, 0))
                result[f"pause_gen_{gen}_count"] = exact_count
                result[f"pause_gen_{gen}_coverage"] = sampled_count / exact_count
            lifetime_count, lifetime_duration_s = lifetime.get(gen, (0, 0.0))
            if lifetime_count > 0:
                result[f"pause_gen_{gen}_lifetime_count"] = lifetime_count
                result[f"pause_gen_{gen}_lifetime_sum"] = dur_to_ms(secs_to_ns(lifetime_duration_s))
        if self._heap_size:
            sorted_heaps = sorted(self._heap_size.values())
            result["heap_size_p99"] = get_quantile_value(sorted_heaps, 99)
        result["pause_count"] = exact_total
        return result

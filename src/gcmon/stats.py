from collections import OrderedDict, deque
from collections.abc import Sequence
from typing import Protocol

try:
    from ddsketch import DDSketch

    HAS_DDSKETCH = True
except ImportError:
    HAS_DDSKETCH = False

from .data import dur_to_us
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


def _record(stats: TStatsData, item: TGCStatsInfo, metric_name: str) -> None:
    metric = METRICS[metric_name]
    ts_start, ts_stop = metric.get_values(item)
    gen = item.gen

    if ts_start != ts_stop:
        stats[metric_name][gen].update(dur_to_us(ts_start, ts_stop))


class StreamingStats:
    MAX_ACTIVE_PIDS = 64
    GENS = (0, 1, 2)

    def __init__(self) -> None:
        self._count: int = 0
        self.metrics: TStatsData = {metric: {gen: Stats() for gen in self.GENS} for metric in METRICS}
        self._metrics_per_pid: OrderedDict[int, TStatsData] = OrderedDict()
        self._materialized_metrics: dict[int, TStatsData] = {}
        self._heap_size: dict[int, int] = {}

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

        _record(self._metrics_per_pid[pid], item, "pause")
        for metric in METRICS:
            _record(self._metrics_per_pid[pid], item, metric)

        self._heap_size[pid] = max(self._heap_size.get(pid, 0), item.heap_size)

    def get_pid_stats(self, pid: int) -> TStatsData | None:
        return self._metrics_per_pid.get(pid) or self._materialized_metrics.get(pid)

    def pids(self) -> set[int]:
        return set(self._metrics_per_pid) | set(self._materialized_metrics)

    def count(self) -> int:
        return self._count

    def aggregate(self) -> dict[str, int | float]:
        result: dict[str, int | float] = {}
        for gen in self.GENS:
            s = self.metrics["pause"][gen]
            if s.count() > 0:
                result[f"pause_gen_{gen}_p99"] = s.percentile(99) / 1_000
                result[f"pause_gen_{gen}_sum"] = s.sum() / 1_000
                result[f"pause_gen_{gen}_count"] = s.count()
        if self._heap_size:
            sorted_heaps = sorted(self._heap_size.values())
            result["heap_size_p99"] = get_quantile_value(sorted_heaps, 99)
        result["pause_count"] = self.count()
        return result

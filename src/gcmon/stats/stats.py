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

from ..model.protocol import (
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
from ..support.time_units import secs_to_ns

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

# (pid, iid, pid_epoch). `pid_epoch` counts the processes that have held the
# pid, from 1, and advances when gcmon sees one exit. Everything a run keeps to
# the end is keyed this way, so a successor's figures never land on its
# predecessor's. Named for the pid because a ring's own index is CPython's
# write cursor into it, which is a different number entirely.
type EpochedRing = tuple[int, int, int]


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


class CumulativeCounters(msgspec.Struct):
    """One ring's counters, counted as the target counts them: from the moment
    its interpreter started, not from the moment gcmon attached.

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
    the exit that ends them and are read together afterwards.

    `metrics` is ``None`` until the ring is admitted, and stays ``None`` if
    the bound declined it. The bound caps sample buffers alone: they hold a
    thousand values per generation per metric, where `loss` and `cumulative`
    hold two numbers per generation each. A declined ring goes on counting,
    so the run totals and the coverage figures stay whole.

    The ring became `declined` when no room for it in running_rings,
    stick unless pid is dead.
    """

    metrics: TStatsData | None = None
    declined: bool = False
    loss: dict[int, LossTotals] = msgspec.field(default_factory=dict)
    cumulative: dict[int, CumulativeCounters] = msgspec.field(default_factory=dict)

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
    # How many interpreters may hold sample buffers at once, one set per
    # (pid, iid) covering that interpreter's three generations. A set costs
    # what it did when the bound counted processes, so the footprint of the
    # processes bounded then buys several interpreters each now. A process
    # that exits settles its buffers and hands the slots back.
    MAX_ACTIVE_RINGS = 256
    GENS = (0, 1, 2)
    # Under this, the sampled percentiles cover too little of the run to leave
    # a reader working it out from the coverage figure, so gcmon says so once.
    COVERAGE_ADVISORY = 0.9

    def __init__(self) -> None:
        self._count: int = 0
        # Phase durations in nanoseconds, per metric and generation.
        self.metrics: TStatsData = {metric: {gen: Stats() for gen in self.GENS} for metric in METRICS}
        # The rings of the processes running now. An entry leaves on the exit
        # that settles it.
        self._running_rings: dict[RingKey, RingStats] = {}
        # The rings of the processes that have exited, settled and kept to
        # the end of the run. Nothing reopens one, so no successor of a reused
        # pid can add to what its predecessor earned.
        self._settled_rings: dict[EpochedRing, RingStats] = {}
        # Running rings holding sample buffers, which is what the bound counts.
        # A ring with only its counters costs too little to bound.
        self._admitted_rings = 0
        # Which process holds each pid, counting from 1.
        self._epoch_per_pid: dict[int, int] = {}
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

        pid_epoch = self._open_pid(pid)
        # Process-wide and one integer per process, so it is kept whether or
        # not the ring behind the record was admitted.
        self._heap_size[(pid, pid_epoch)] = max(self._heap_size.get((pid, pid_epoch), 0), item.heap_size)

        ring = self._open_ring(pid, item.iid)
        metrics = ring.metrics or self._admit(ring, (pid, item.iid))
        if metrics is None:
            return

        for metric in METRICS:
            _record(metrics, item, metric)

    def _open_ring(self, pid: int, iid: int) -> RingStats:
        """The ring the records arriving now belong to, opened if new.

        Every ring gets one, since loss and cumulative totals are due from a
        ring the bound turned away as much as from one it admitted.
        """
        key = (pid, iid)
        ring = self._running_rings.get(key)
        if ring is None:
            ring = RingStats()
            self._running_rings[key] = ring
        return ring

    def _open_pid(self, pid: int) -> int:
        """Mark *pid* as running, and answer which process holding it the
        records arriving now belong to.

        A record reaches gcmon only from a process that is running, so its
        arrival is what opens the pid. :meth:`materialize` closes it again.
        The epoch counts from 1 and advances on the exit gcmon sees, so a
        successor files everything apart from its predecessor.
        """
        self._open_pids.add(pid)
        return self._epoch_per_pid.setdefault(pid, 1)

    def _latest_epoch(self, pid: int) -> int:
        """Which process a reader naming no epoch means: the one running, or
        the last one that ran."""
        pid_epoch = self._epoch_per_pid.get(pid, 1)
        return pid_epoch if pid in self._open_pids else pid_epoch - 1

    def _admit(self, ring: RingStats, key: RingKey) -> TStatsData | None:
        """Give *ring* its sample buffers, or ``None`` where none are free.

        A ring gets them on its first record and keeps them until its process
        exits. ``None`` means this record and every later one is measured into
        the run totals and the ring's own counters alone, which happens when
        `MAX_ACTIVE_RINGS` interpreters were already running with buffers of
        their own at the moment this ring appeared.

        Either way what a ring samples covers one process's interpreter over
        one unbroken stretch, so its sampled count and its percentiles always
        describe the same records.
        """
        if ring.declined:
            # Declined once, declined for as long as this entry stands. A slot
            # freed by another process's exit would otherwise start sampling
            # this ring midway through its life, and nothing in what it kept
            # would say where the sampling began.
            return None

        if self._admitted_rings >= self.MAX_ACTIVE_RINGS:
            self._decline(ring, key)
            return None

        ring.metrics = {metric: {gen: Stats() for gen in self.GENS} for metric in METRICS}
        self._admitted_rings += 1
        return ring.metrics

    def _decline(self, ring: RingStats, key: RingKey) -> None:
        """Note that this ring keeps no sampled metrics, saying why the first
        time."""
        ring.declined = True
        if self._bound_warned:
            return

        self._bound_warned = True
        logger.warning(
            "PID %s interpreter %s: gcmon already holds detailed statistics for %s running "
            "interpreters, the most it keeps at once. Records read from any further interpreter are "
            "counted in the run totals, and gcmon keeps no detailed statistics of its own for it.",
            *key,
            self.MAX_ACTIVE_RINGS,
        )

    def materialize(self, pid: int) -> None:
        """Settle every ring of *pid*, which has exited, and advance its epoch.

        Whatever claims the pid next reads the advanced epoch and starts
        clean, with sample buffers and totals of its own.
        """
        if pid not in self._open_pids:
            return

        self._settle(pid, [key for key in self._running_rings if key[0] == pid])

    def _settle(self, pid: int, keys: list[RingKey]) -> None:
        """Close *pid*, which is open, and settle *keys*, which are its rings
        and no other pid's.
        """
        pid_epoch = self._epoch_per_pid.get(pid, 1)
        self._open_pids.discard(pid)
        self._epoch_per_pid[pid] = pid_epoch + 1

        for key in keys:
            settled = self._running_rings.pop(key)
            if settled.metrics is not None:
                self._admitted_rings -= 1
            settled.settle()
            self._settled_rings[(*key, pid_epoch)] = settled

    def retain(self, pids: Set[int]) -> None:
        """Settle every ring whose process is not in *pids*.

        A pid missing from the caller's per-tick listing of the target's
        children has gone.
        """
        departed = self._open_pids - set(pids)
        if not departed:
            return

        pid_keys: dict[int, list[RingKey]] = {pid: [] for pid in departed}
        for key in self._running_rings:
            keys = pid_keys.get(key[0])
            if keys is not None:
                keys.append(key)

        for pid, keys in pid_keys.items():
            self._settle(pid, keys)

    def record_read_time(self, duration_ns: int) -> None:
        self._read_time.update(duration_ns)

    def record_loss(self, pid: int, iid: int, gen: int, lost_count: int, lost_pause_ns: int) -> None:
        """Record one interval's worth of records gcmon did not read.

        `record_loss` hands over one poll's gap at a time, so these sum.
        Sampled plus lost is the exact total ADR-0015 defines, so the rings
        themselves stay in the monitor.
        """
        self._open_pid(pid)
        ring = self._open_ring(pid, iid)
        ring.loss.setdefault(gen, LossTotals()).add(lost_count, lost_pause_ns)

    def low_coverage(self, pid: int) -> tuple[int, int, float] | None:
        """The least covered ring of *pid* when it sits under
        `COVERAGE_ADVISORY`, as its interpreter, its generation and its
        coverage. ``None`` on a healthy run.

        Only the rings running now, which are the pid's, since the caller
        polled it.
        """
        worst: tuple[int, int, float] | None = None
        for (ring_pid, iid), ring in self._running_rings.items():
            if ring_pid != pid or ring.declined:
                # A declined ring has a sampled count of zero here, so the
                # advisory has nothing to say about it.
                continue
            for gen, lost in ring.loss.items():
                if not lost.count:
                    continue

                sampled = ring.sampled(gen).count()
                coverage = sampled / (sampled + lost.count)
                if coverage < self.COVERAGE_ADVISORY and (worst is None or coverage < worst[2]):
                    worst = (iid, gen, coverage)
        return worst

    def observe_cumulative(self, pid: int, iid: int, gen: int, collections: int, duration_s: float) -> None:
        """Take one ring's totals since its interpreter started.

        The target counts both of them cumulatively, so the newest values
        replace the previous ones; this observes a counter rather than
        appending to one, which is what separates it from `record_loss`. A
        successor on a reused pid writes into an entry of its own, so the fold
        adds the two rather than losing the larger history to the smaller one
        that follows it.
        """
        self._open_pid(pid)
        self._open_ring(pid, iid).cumulative[gen] = CumulativeCounters(collections, duration_s)

    def pause_totals(self, pid: int, iid: int, gen: int, pid_epoch: int | None = None) -> PauseTotals:
        """One ring, read once.

        *pid_epoch* names which process held the pid; left out, it reads the
        one running now or the last one that ran. Every ring at once is
        :meth:`pause_totals_by_gen`, which costs a pass instead.
        """
        ring = self._find_ring(pid, iid, pid_epoch)
        if ring is None:
            return PauseTotals()
        return ring.pause_totals(gen)

    def _all_rings(self) -> Iterator[RingStats]:
        """Every ring of the run, running and settled alike."""
        return chain(self._running_rings.values(), self._settled_rings.values())

    def pause_totals_by_gen(self) -> dict[int, PauseTotals]:
        """Every generation's pause totals over every ring."""
        # Folded here rather than behind a helper, which had this one caller.
        lost: dict[int, LossTotals] = {}
        for ring in self._all_rings():
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

    def cumulative_scope(self) -> tuple[int, int]:
        """How many interpreters, in how many processes, the cumulative fold
        covers.

        A caller states both alongside the fold, so a reader can tell one
        interpreter's history from a sum over five that started at different
        moments.

        Two processes that shared a pid count as two, since the epoch tells
        them apart. A pid gcmon never saw exit still counts as one.
        """
        interpreters = {
            key for key, ring in self._keyed_rings() if any(totals.collections for totals in ring.cumulative.values())
        }
        return len(interpreters), len({(pid, pid_epoch) for pid, _iid, pid_epoch in interpreters})

    def cumulative_totals_by_gen(self) -> dict[int, CumulativeCounters]:
        """Fold every ring's cumulative counters into a per-gen total, single
        pass."""
        by_gen: dict[int, CumulativeCounters] = {}
        for ring in self._all_rings():
            for gen, totals in ring.cumulative.items():
                by_gen.setdefault(gen, CumulativeCounters()).add(totals.collections, totals.duration_s)
        return by_gen

    @property
    def read_time(self) -> Stats:
        """Read durations in nanoseconds, over every polled pid."""
        return self._read_time

    def _find_ring(self, pid: int, iid: int, pid_epoch: int | None = None) -> RingStats | None:
        """One ring, running or settled, or ``None`` where the run has none.

        *pid_epoch* names which process held the pid, counting from 1. Left
        out, it reads the one running now or the last one that ran.
        """
        if pid_epoch is None:
            pid_epoch = self._latest_epoch(pid)
        if pid in self._open_pids and pid_epoch == self._epoch_per_pid.get(pid, 1):
            return self._running_rings.get((pid, iid))
        return self._settled_rings.get((pid, iid, pid_epoch))

    def _keyed_rings(self) -> Iterator[tuple[EpochedRing, RingStats]]:
        """Every ring of the run under the key a caller names it by."""
        for (pid, iid), ring in self._running_rings.items():
            yield (pid, iid, self._epoch_per_pid.get(pid, 1)), ring
        yield from self._settled_rings.items()

    def get_ring_stats(self, pid: int, iid: int, pid_epoch: int | None = None) -> TStatsData | None:
        """One interpreter's sampled metrics, still filling or settled.

        ``None`` where the ring has none, which is a key gcmon never read or a
        ring the bound declined.
        """
        ring = self._find_ring(pid, iid, pid_epoch)
        return ring.metrics if ring is not None else None

    def rings(self) -> list[EpochedRing]:
        """Every ring holding sampled metrics, in key order.

        One entry per process that held the pid, so a reused pid brings one
        for each. A ring the bound declined holds none and is absent;
        :meth:`untracked_rings` counts those.
        """
        return sorted(key for key, ring in self._keyed_rings() if ring.metrics is not None)

    def untracked_rings(self) -> int:
        """How many rings reached `update` with no slot to take.

        Their records are in the run totals and in the coverage figures, so a
        caller can state the count rather than leave a reader adding the rings
        up and finding them short.
        """
        return sum(1 for ring in self._all_rings() if ring.declined)

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

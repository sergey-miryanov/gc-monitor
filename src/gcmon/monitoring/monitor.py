"""Polling a process for GC records and passing them to the stats and the
exporters."""

import logging
import time
from _remote_debugging import get_child_pids
from collections.abc import Callable, Sequence, Set
from itertools import groupby
from typing import Self

import msgspec

from ..exporters import EventsExporter
from ..model.data import GenLoss, LossMsg
from ..model.loss import (
    RingAccumulator,
    RingKey,
)
from ..model.poll_status import PollStatus
from ..model.process import Process
from ..model.protocol import TGCStatsInfo
from ..stats.streaming_stats import StreamingStats
from .events_reader import EventsReader, TargetUnavailable
from .process_registry import ProcessRegistry
from .target_process import TargetProcess
from .wait_policy import WaitPolicy, WaitPolicyFactory

logger = logging.getLogger("gcmon")

__all__ = ["EventsMonitor", "PollReport"]


def _is_complete(record: TGCStatsInfo) -> bool:
    """False for a slot holding no finished record: never written, or
    mid-write with ``ts_start`` published and ``ts_stop`` not yet."""
    return record.ts_start < record.ts_stop


class PidState(msgspec.Struct):
    """What gcmon carries from one poll of a process to the next."""

    rings: dict[RingKey, RingAccumulator] = msgspec.field(default_factory=dict)
    # None before the first read. Two polls bound a loss record, so one poll
    # bounds nothing.
    ts_last_poll: int | None = None


class PollReport(msgspec.Struct):
    """What one tick of monitoring found.

    ``live`` answered :attr:`PollStatus.OK`. For a process that never
    collects, a successful read is the only evidence gcmon has that it existed,
    which is what liveness reporting rests on (ADR-0011).

    ``keep_running`` is false once no wait policy wants the run to go on.
    """

    live: frozenset[Process]
    keep_running: bool


class EventsMonitor:
    def __init__(
        self,
        process: TargetProcess,
        exporter: EventsExporter,
        stats: StreamingStats,
        *,
        reader: EventsReader,
        wait_policy_factory: WaitPolicyFactory,
        is_pid_enabled: Callable[[int], bool] | None = None,
        registry: ProcessRegistry | None = None,
    ) -> None:
        """
        *reader* reads a process's records.

        *wait_policy_factory* builds the per-pid policy that decides when a pid
        is finished.

        *is_pid_enabled* is the control plane's per-pid verdict: ``False`` means
        the control server has suppressed that pid and it must not be polled.
        ``None`` means no control plane.

        *registry* mints the `Process` every record is filed under, one per
        run. A caller with none gets one of its own.
        """
        self._process = process
        self._exporter = exporter
        self._enabled = True
        self._pids: dict[int, PidState] = {}
        self._policies: dict[int, WaitPolicy] = {}
        self._reader = reader
        self._wait_policy_factory = wait_policy_factory
        self._is_pid_enabled = is_pid_enabled
        self._stats = stats
        self._processes = registry if registry is not None else ProcessRegistry()
        self._coverage_warned = False

    def tick(self, now_ns: int, stop: Callable[[], bool]) -> PollReport:
        """Poll the target and every child once, and report what answered.

        Prunes the state of every pid that has left the process tree first, so
        a reused pid inherits nothing from the process before it.

        *now_ns* stamps the whole tick, liveness included. The caller reads the
        clock once and hands the same instant to the RSS sampler.

        *stop* is asked between pids, so a shutdown does not have to wait out a
        whole process tree.
        """
        child_pids = self._get_child_pids()
        children = [self._process.pid, *(child_pids or [])]

        # A process that exits between two ticks is never polled again, so no
        # policy gives up on it and the branch below never runs. None means the
        # listing failed, so prune only when it worked.
        if child_pids is not None:
            self._retain(set(children), now_ns)

        live: set[Process] = set()
        keep_running = False
        for pid in children:
            if stop():
                break

            if self._is_pid_enabled is not None and not self._is_pid_enabled(pid):
                continue

            policy = self._policies.get(pid)
            if policy is None:
                policy = self._policies[pid] = self._wait_policy_factory()

            # A pid enters the registry when it is about to be polled, not
            # when the listing names it: a suppressed pid produces no
            # records and needs no process.
            process = self._processes.current(pid) or self._processes.create(pid, now_ns)

            rc = self._poll(process)
            keep_waiting = policy.wait(rc)
            keep_running = keep_running or keep_waiting
            if rc == PollStatus.OK:
                live.add(process)
            elif not keep_waiting:
                # The policy stays behind. A fresh one answers True until its
                # own startup timeout expires, holding the run open.
                self._forget(pid, now_ns)

        live_processes = frozenset(live)

        # After the poll phase, one batched call, skipped on an empty set.
        # ADR-0011 argues all three.
        if live_processes:
            self._exporter.add_process_liveness({process.pid for process in live_processes}, now_ns)

        return PollReport(live=live_processes, keep_running=keep_running)

    def _get_child_pids(self) -> list[int] | None:
        """Every descendant of the target, or ``None`` when the read failed.

        An empty list means no children. ``None`` means no answer, so a caller
        pruning state for missing pids skips that tick.
        """
        try:
            return get_child_pids(self._process.pid, recursive=True)
        except Exception as exc:
            logger.warning(
                "Monitor for PID %s encountered error while gathering children PIDs", self._process.pid, exc_info=exc
            )
            return None

    def _poll(self, process: Process) -> PollStatus:
        pid = process.pid

        if not self._enabled:
            logger.warning(
                "Monitor for PID %s already stopped",
                pid,
            )
            return PollStatus.FAIL

        try:
            ts_read_start = time.monotonic_ns()
            records = self._reader.read(pid)
            ts_read_stop = time.monotonic_ns()
            self._stats.record_read_time(ts_read_stop - ts_read_start)
            self._ingest(process, records, ts_read_start)

            return PollStatus.OK
        except TargetUnavailable as exc:
            # The ordinary end of a run reaches here, so it stays at debug
            # level: a warning would put a traceback on stderr every time a
            # target exits. ADR-0020.
            logger.debug("Error while polling PID %s (child PID=%s): %s", self._process.pid, pid, exc)
            return PollStatus.INVALID_PROCESS
        except Exception as exc:
            logger.warning("Monitor for PID %s (child PID=%s) encountered error", self._process.pid, pid, exc_info=exc)
            return PollStatus.FAIL

    def _forget(self, pid: int, ts: int) -> None:
        """Drop the cursors and the attachment held for *pid*, so a reused pid
        inherits no counter, no poll instant and no debug offsets from the
        process before it. The policy stays; see :meth:`tick`.
        """
        self._pids.pop(pid, None)
        self._reader.forget(pid)
        process = self._processes.current(pid)
        if process is not None:
            # Settle before retiring: the rings file under the process that
            # earned them, and a retirement takes it out of the registry.
            self._stats.materialize(process)
            self._processes.retire(pid, ts)

    def _retain(self, pids: Set[int], ts: int) -> None:
        """Drop the state of every pid outside *pids*, all of it at once.

        A cursor outliving its policy, or the reverse, is the disagreement
        ADR-0017 rules out, and ADR-0020 puts the reader's attachment under the
        same rule. A process that exits between two ticks is never polled again,
        so no policy gives up on it and this drops it instead.
        """
        for pid in self._pids.keys() - pids:
            del self._pids[pid]
        for pid in self._policies.keys() - pids:
            del self._policies[pid]
        self._reader.retain(pids)
        self._stats.retain({process for process in self._processes.live() if process.pid in pids})
        self._processes.retain(pids, ts)

    def _ingest(self, process: Process, records: Sequence[TGCStatsInfo], ts_poll: int) -> None:
        """Emit the records in *records* not seen yet.

        Every poll returns the whole ring buffer, so ``collections`` is what
        identifies a record.

        *ts_poll* is when this read began. It closes the interval the previous
        poll opened, see ADR-0015.
        """
        pid = process.pid
        state = self._pids.setdefault(pid, PidState())
        ts_prev_poll = state.ts_last_poll
        state.ts_last_poll = ts_poll

        # Ring buffer records arrive wrapped, with the generations
        # concatenated, so restore each ring's counter order.
        ordered = sorted(
            (record for record in records if _is_complete(record)),
            key=lambda record: (record.iid, record.gen, record.collections),
        )

        fresh: list[TGCStatsInfo] = []
        gens_by_iid: dict[int, list[GenLoss]] = {}
        for (iid, gen), group in groupby(ordered, key=lambda record: (record.iid, record.gen)):
            accumulator = state.rings.setdefault((iid, gen), RingAccumulator())
            unseen = accumulator.unseen(group)
            if not unseen:
                continue

            gen_loss = accumulator.ingest(unseen)
            gens_by_iid.setdefault(iid, []).append(gen_loss)
            self._stats.observe_cumulative(process, iid, gen, accumulator.last_collections, accumulator.last_duration)
            if gen_loss.lost_count:
                self._stats.record_loss(process, iid, gen, gen_loss.lost_count, gen_loss.lost_pause_ns)
            fresh.extend(unseen)

        for iid, gens in gens_by_iid.items():
            if all(gen_loss.no_loss for gen_loss in gens):
                continue

            # A first poll seeds every ring it touches, and seeding opens no
            # gap, so no interval reaches here on one.
            assert ts_prev_poll is not None
            self._exporter.add_loss_event(process, LossMsg(iid=iid, ts_start=ts_prev_poll, ts_stop=ts_poll, gens=gens))

        # We want to keep exported events in the time order
        for record in sorted(fresh, key=lambda record: (record.iid, record.ts_start)):
            self._exporter.add_event(process, record)
            self._stats.update(process, record)

        self._warn_low_coverage(process)

    def _warn_low_coverage(self, process: Process) -> None:
        """Say once per run that gcmon is reading too little of its target.

        What to do about it is not said here: it turns on whether the loop is
        holding its schedule, which this cannot know when it fires. The
        end-of-run summary carries the remedy; see ADR-0019.
        """
        if self._coverage_warned:
            return

        low = self._stats.low_coverage(process)
        if low is None:
            return

        iid, gen, coverage = low
        self._coverage_warned = True
        logger.warning(
            "PID %s interpreter %s generation %s: only %s%% of collections observed so far. Counts "
            "and sums are reconstructed and exact; percentiles cover only what was sampled and read "
            "high.",
            process.pid,
            iid,
            gen,
            # Truncated: 89.6% would read as "90%", the floor this fires
            # below. The inner round absorbs float error, which takes an exact
            # 29 of 100 to 28.999999999999996 and would print it as 28%.
            int(round(coverage * 100, 6)),
        )

    def stop(self) -> None:
        """Close the exporter, let go of every attachment, and stop accepting
        polls.

        The reader is pruned here and not left to garbage collection because an
        attachment is a handle on somebody else's process: on Windows it holds
        the pid reserved for as long as gcmon keeps it (ADR-0020), and a monitor
        that has stopped monitoring should not be doing that.

        Safe to call more than once.
        """
        self._exporter.close()
        self._reader.retain(frozenset())
        self._enabled = False

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def pid(self) -> int:
        return self._process.pid

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()

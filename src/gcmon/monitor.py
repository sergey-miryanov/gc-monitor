"""Polling a process for GC records and passing them to the stats and the
exporters."""

import logging
import time
from _remote_debugging import get_child_pids, get_gc_stats
from collections.abc import Callable, Sequence, Set
from itertools import groupby
from typing import Self

import msgspec

from .data import GenLoss, LossMsg
from .exporters import EventsExporter
from .loss import (
    RingAccumulator,
    RingKey,
)
from .poll_status import PollStatus
from .protocol import TGCStatsInfo
from .stats import StreamingStats
from .target_process import TargetProcess
from .wait_policy import WaitPolicy, WaitPolicyFactory

logger = logging.getLogger("gcmon")

__all__ = ["EventsMonitor", "PollReport"]


def _is_complete(event: TGCStatsInfo) -> bool:
    """False for a slot holding no finished record: never written, or
    mid-write with ``ts_start`` published and ``ts_stop`` not yet."""
    return event.ts_start < event.ts_stop


class PidState(msgspec.Struct):
    """What gcmon carries from one poll of a process to the next."""

    rings: dict[RingKey, RingAccumulator] = msgspec.field(default_factory=dict)
    # None before the first read. Two polls bound a loss record, so one poll
    # bounds nothing.
    ts_last_poll: int | None = None


class PollReport(msgspec.Struct):
    """What one tick of monitoring found.

    ``live_pids`` is the set that answered :attr:`PollStatus.OK`. A successful
    read is the only evidence gcmon has that a process was still there, and
    for a process that never collects it is the *only* evidence of any kind,
    so it is what liveness reporting is built on (ADR-0011).

    ``keep_running`` is false once no wait policy still wants the run to go
    on, which is the caller's signal to stop.
    """

    live_pids: frozenset[int]
    keep_running: bool


class EventsMonitor:
    def __init__(
        self,
        process: TargetProcess,
        exporter: EventsExporter,
        stats: StreamingStats,
        *,
        wait_policy_factory: WaitPolicyFactory,
        is_pid_enabled: Callable[[int], bool] | None = None,
    ) -> None:
        """
        *wait_policy_factory* builds the per-pid policy that decides when a pid
        is finished. It has no default; ADR-0017 says why.

        *is_pid_enabled* is the control plane's per-pid verdict: ``False`` means
        the control server has suppressed that pid and it must not be polled.
        ``None`` means no control plane.
        """
        self._process = process
        self._exporter = exporter
        self._enabled = True
        self._pids: dict[int, PidState] = {}
        self._policies: dict[int, WaitPolicy] = {}
        self._wait_policy_factory = wait_policy_factory
        self._is_pid_enabled = is_pid_enabled
        self._stats = stats
        self._coverage_warned = False

    def tick(self, now_ns: int, stop: Callable[[], bool]) -> PollReport:
        """Poll the target and every child once, and report what answered.

        Prunes the state of every pid that has left the process tree first, so
        a reused pid inherits nothing from the process before it. Why the
        monitor owns all of this rather than the loop: ADR-0017.

        *now_ns* stamps the whole tick, liveness included. The caller reads the
        clock once and hands the same instant to the RSS sampler in seconds
        (ADR-0011, ADR-0013).

        *stop* is asked between pids, so a shutdown does not have to wait out a
        whole process tree.
        """
        child_pids = self.get_child_pids()
        children = [self._process.pid, *(child_pids or [])]

        # A process that exits between two ticks is never polled again, so no
        # policy gives up on it and the branch below never runs. None means
        # the listing failed, so prune only when it worked.
        if child_pids is not None:
            self._retain(set(children))

        live: set[int] = set()
        keep_running = False
        for pid in children:
            if stop():
                break

            if self._is_pid_enabled is not None and not self._is_pid_enabled(pid):
                continue

            policy = self._policies.get(pid)
            if policy is None:
                policy = self._policies[pid] = self._wait_policy_factory()

            rc = self.poll(pid)
            keep_waiting = policy.wait(rc)
            keep_running = keep_running or keep_waiting
            if rc == PollStatus.OK:
                live.add(pid)
            elif not keep_waiting:
                # The policy decides when a pid is finished. It stays behind:
                # a fresh one would answer True until its own startup timeout
                # expired, holding the run open.
                self._forget(pid)

        live_pids = frozenset(live)

        # After the poll phase and never during it, in one batched call, and
        # skipped on an empty set. All three are ADR-0011's, which explains
        # what each of them buys.
        if live_pids:
            self._exporter.add_process_liveness(live_pids, now_ns)

        return PollReport(live_pids=live_pids, keep_running=keep_running)

    def get_child_pids(self) -> list[int] | None:
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

    def poll(self, pid: int) -> PollStatus:

        if not self._enabled:
            logger.warning(
                "Monitor for PID %s already stopped",
                pid,
            )
            return PollStatus.FAIL

        try:
            ts_read_start = time.monotonic_ns()
            events = get_gc_stats(pid, all_interpreters=True)
            ts_read_stop = time.monotonic_ns()
            self._stats.record_read_time(ts_read_stop - ts_read_start)
            self._ingest(pid, events, ts_read_start)

            return PollStatus.OK
        except RuntimeError as exc:
            logger.debug("Error while polling PID %s (child PID=%s): %s", self._process.pid, pid, exc)
            return PollStatus.INVALID_PROCESS
        except PermissionError as exc:
            logger.debug("Error while polling PID %s (child PID=%s): %s", self._process.pid, pid, exc)
            return PollStatus.INVALID_PROCESS
        except Exception as exc:
            logger.warning("Monitor for PID %s (child PID=%s) encountered error", self._process.pid, pid, exc_info=exc)
            return PollStatus.FAIL

    def _forget(self, pid: int) -> None:
        """Drop the cursors held for *pid*, so a reused pid inherits no counter
        and no poll instant from the process before it.

        The policy is deliberately left behind; see :meth:`tick`.
        """
        self._pids.pop(pid, None)
        self._stats.materialize(pid)

    def _retain(self, pids: Set[int]) -> None:
        """Drop the state of every pid outside *pids*.

        Every per-pid thing at once, which is the point: a cursor outliving
        its policy, or the reverse, is the disagreement spec 0038 exists to
        make impossible. A process that exits between two ticks is never
        polled again, so no wait policy gives up on it and this is the only
        thing that drops it.
        """
        for pid in self._pids.keys() - pids:
            del self._pids[pid]
        for pid in self._policies.keys() - pids:
            del self._policies[pid]
        self._stats.retain(pids)

    def _ingest(self, pid: int, events: Sequence[TGCStatsInfo], ts_poll: int) -> None:
        """Emit the records in *events* not seen yet.

        Every poll returns the whole ring buffer, so ``collections`` is what
        identifies a record.

        *ts_poll* is when this read began. It closes the interval the previous
        poll opened, see ADR-0015.
        """
        state = self._pids.setdefault(pid, PidState())
        ts_prev_poll = state.ts_last_poll
        state.ts_last_poll = ts_poll

        # Ring buffer records arrive wrapped, with the generations
        # concatenated, so restore each ring's counter order.
        ordered = sorted(
            (event for event in events if _is_complete(event)),
            key=lambda event: (event.iid, event.gen, event.collections),
        )

        fresh: list[TGCStatsInfo] = []
        gens_by_iid: dict[int, list[GenLoss]] = {}
        for (iid, gen), group in groupby(ordered, key=lambda event: (event.iid, event.gen)):
            accumulator = state.rings.setdefault((iid, gen), RingAccumulator())
            unseen = accumulator.unseen(group)
            if not unseen:
                continue

            gen_loss = accumulator.ingest(unseen)
            gens_by_iid.setdefault(iid, []).append(gen_loss)
            self._stats.observe_cumulative(pid, iid, gen, accumulator.last_collections, accumulator.last_duration)
            if gen_loss.lost_count:
                self._stats.record_loss(pid, iid, gen, gen_loss.lost_count, gen_loss.lost_pause_ns)
            fresh.extend(unseen)

        for iid, gens in gens_by_iid.items():
            if all(gen_loss.no_loss for gen_loss in gens):
                continue

            # A first poll seeds every ring it touches, and seeding opens no
            # gap, so no interval reaches here on one.
            assert ts_prev_poll is not None
            self._exporter.add_loss_event(pid, LossMsg(iid=iid, ts_start=ts_prev_poll, ts_stop=ts_poll, gens=gens))

        # We want to keep exported events in the time order
        for event in sorted(fresh, key=lambda event: (event.iid, event.ts_start)):
            self._exporter.add_event(pid, event)
            self._stats.update(pid, event)

        self._warn_low_coverage(pid)

    def _warn_low_coverage(self, pid: int) -> None:
        """Say once per run that gcmon is reading too little of its target."""
        if self._coverage_warned:
            return

        low = self._stats.low_coverage(pid)
        if low is None:
            return

        iid, gen, coverage = low
        self._coverage_warned = True
        logger.warning(
            "PID %s interpreter %s generation %s: only %s%% of collections observed so far. Counts "
            "and sums are reconstructed and exact; percentiles cover only what was sampled and read "
            "high. Polling more often (a smaller --rate) may observe more, unless the target "
            "collects faster than gcmon can poll.",
            pid,
            iid,
            gen,
            # Truncated: 89.6% would read as "90%", the floor this fires
            # below. The inner round absorbs float error, which takes an exact
            # 29 of 100 to 28.999999999999996 and would print it as 28%.
            int(round(coverage * 100, 6)),
        )

    def stop(self) -> None:
        """Close the exporter and stop accepting polls.

        Safe to call more than once.
        """
        self._exporter.close()
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

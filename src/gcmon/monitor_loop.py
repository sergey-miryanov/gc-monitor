import logging
import threading
import time
from collections.abc import Callable
from typing import Self

from .monitor import EventsMonitor
from .poll_status import PollStatus
from .rss_sampler import RssSampler
from .run_policy import Runner
from .utils import set_on_exit
from .wait_policy import WaitPolicy, WaitPolicyFactory

logger = logging.getLogger("gcmon")

__all__ = ["MonitorLoop"]


class MonitorLoop:
    def __init__(
        self,
        monitor: EventsMonitor,
        runner: Runner,
        wait_policy_factory: WaitPolicyFactory,
        rate: float = 0.1,
        enabled: Callable[[int], bool] | None = None,
        rss_sampler: RssSampler | None = None,
    ) -> None:
        self._monitor = monitor
        self._runner = runner
        self._wait_policy_factory = wait_policy_factory
        self._rate = rate
        self._stop_event = threading.Event()
        self._enabled = enabled
        self._rss_sampler = rss_sampler

    def close(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        pid_policies: dict[int, WaitPolicy] = {}
        with set_on_exit(self._stop_event):
            for _ in self._runner.run(self._stop_event.is_set):
                # One clock read per tick: liveness stamps the trace in
                # nanoseconds, the RSS sampler paces itself in seconds.
                now_ns = time.monotonic_ns()
                now = now_ns / 1e9
                wait: list[bool] = []
                child_pids = self._monitor.get_child_pids()
                children: list[int] = [self._monitor.pid, *(child_pids or [])]

                # A process that exits between two ticks drops out of the
                # tree without ever being polled again, so no policy gives up
                # on it and the branch below never runs. Absence from the
                # tree is the only evidence, and it counts only when the
                # listing worked: get_child_pids returns None, not an empty
                # tree, when it could not ask.
                if child_pids is not None:
                    self._monitor.retain(set(children))
                    for gone in pid_policies.keys() - set(children):
                        del pid_policies[gone]

                # Phase 1: GC poll — track which PIDs returned OK
                live_pids: set[int] = set()
                for pid in children:
                    if self._stop_event.is_set():
                        break

                    if self._enabled is not None and not self._enabled(pid):
                        continue

                    if pid not in pid_policies:
                        pid_policies[pid] = self._wait_policy_factory()

                    rc = self._monitor.poll(pid)
                    keep_waiting = pid_policies[pid].wait(rc)
                    wait.append(keep_waiting)
                    if rc == PollStatus.OK:
                        live_pids.add(pid)
                    elif not keep_waiting:
                        # The policy decides when a pid is finished, so it
                        # decides when the cursors built from that process's
                        # counter stop meaning anything. The policy itself
                        # stays: a replacement would not have seen this pid
                        # alive and would answer True until its own startup
                        # timeout expired, holding the loop open that long.
                        self._monitor.forget(pid)

                # Phase 2: liveness — report who answered, in one batched
                # call. The only place that knows a process was still
                # there, so a pid that never collects reaches the trace
                # through here or not at all.
                if live_pids:
                    self._monitor.exporter.add_process_liveness(live_pids, now_ns)

                # Phase 3: RSS — sample live PIDs if interval elapsed
                if self._rss_sampler:
                    self._rss_sampler.tick(now, live_pids)

                if not any(wait):
                    break

                # Wait for next polling interval
                self._stop_event.wait(timeout=self._rate)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

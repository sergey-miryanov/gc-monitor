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
                # One clock read per tick, shared by both phases below:
                # liveness stamps the trace in nanoseconds (ADR-0009),
                # the RSS sampler paces itself in seconds (ADR-0013).
                now_ns = time.monotonic_ns()
                now = now_ns / 1e9
                wait: list[bool] = []
                children: list[int] = [self._monitor.pid, *self._monitor.get_child_pids()]

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
                    wait.append(pid_policies[pid].wait(rc))
                    if rc == PollStatus.OK:
                        live_pids.add(pid)

                # Phase 2: liveness — report who answered, in one batched
                # call. This is the only place in gcmon that knows a
                # process was still there, so a pid that never collects
                # reaches the trace through here or not at all.
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

import logging
import threading
from collections.abc import Callable
from typing import Self

from .monitor import EventsMonitor
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
    ) -> None:
        self._monitor = monitor
        self._runner = runner
        self._wait_policy_factory = wait_policy_factory
        self._rate = rate
        self._stop_event = threading.Event()
        self._enabled = enabled

    def close(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        pid_policies: dict[int, WaitPolicy] = {}
        seen_pids: set[int] = set()
        with set_on_exit(self._stop_event):
            for _ in self._runner.run(self._stop_event.is_set):
                wait: list[bool] = []
                children: list[int] = [self._monitor.pid, *self._monitor.get_child_pids()]
                current_pids = set(children)

                # Detect pids that were previously polled but no longer
                # appear in the children list (process died between
                # iterations). The next ``poll()`` for them would never
                # run, so ``EventsMonitor.poll()`` cannot emit DIED via
                # the normal INVALID_PROCESS path. The monitor exposes
                # ``mark_pid_died`` exactly for this signal.
                vanished = seen_pids - current_pids
                for pid in vanished:
                    if self._monitor.mark_pid_died(pid):
                        logger.debug(
                            "Child PID %s no longer present in children list; emitted DIED",
                            pid,
                        )
                    pid_policies.pop(pid, None)

                seen_pids = current_pids

                for pid in children:
                    if self._stop_event.is_set():
                        break

                    if self._enabled is not None and not self._enabled(pid):
                        continue

                    if pid not in pid_policies:
                        pid_policies[pid] = self._wait_policy_factory()

                    rc = self._monitor.poll(pid)
                    wait.append(pid_policies[pid].wait(rc))

                if not any(wait):
                    break

                # Wait for next polling interval
                self._stop_event.wait(timeout=self._rate)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

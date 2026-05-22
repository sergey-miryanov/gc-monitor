import logging
import threading
from typing import Any, Self

from .monitor import EventsMonitor
from .run_policy import Runner
from .utils import set_on_exit
from .wait_policy import WaitPolicy

logger = logging.getLogger("gc_monitor.monitor_loop")

__all__ = ["MonitorLoop"]


class MonitorLoop:
    def __init__(
        self,
        monitor: EventsMonitor,
        runner: Runner,
        wait_policy: WaitPolicy,
        rate: float = 0.1,
    ) -> None:
        self._monitor = monitor
        self._runner = runner
        self._wait_policy = wait_policy
        self._rate = rate
        self._stop_event = threading.Event()

    def close(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        with self._monitor, set_on_exit(self._stop_event):
            for _ in self._runner.run(self._stop_event.is_set):
                wait: list[bool] = []
                children: list[int] = [self._monitor.pid, *self._monitor.get_child_pids()]
                for pid in children:
                    if self._stop_event.is_set():
                        break

                    rc = self._monitor.poll(pid)
                    wait.append(self._wait_policy.wait(rc))

                if not any(wait):
                    break

                # Wait for next polling interval
                self._stop_event.wait(timeout=self._rate)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, type_: Any, value: Any, traceback: Any) -> None:
        self.close()

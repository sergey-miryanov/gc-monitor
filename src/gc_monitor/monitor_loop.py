import logging
import threading
from collections.abc import Callable
from typing import Self

from .monitor import EventsMonitor
from .run_policy import Runner
from .utils import set_on_exit
from .wait_policy import WaitPolicy

logger = logging.getLogger("gc_monitor")

__all__ = ["MonitorLoop"]


class MonitorLoop:
    def __init__(
        self,
        monitor: EventsMonitor,
        runner: Runner,
        wait_policy: WaitPolicy,
        rate: float = 0.1,
        enabled: Callable[[int], bool] | None = None,
    ) -> None:
        self._monitor = monitor
        self._runner = runner
        self._wait_policy = wait_policy
        self._rate = rate
        self._stop_event = threading.Event()
        self._enabled = enabled

    def close(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        with set_on_exit(self._stop_event):
            logger.debug("Monitor loop enter")
            for _ in self._runner.run(self._stop_event.is_set):
                logger.debug("Monitor loop iter")
                wait: list[bool] = []
                children: list[int] = [self._monitor.pid, *self._monitor.get_child_pids()]
                logger.debug("Monitor loop children: %s", children)
                for pid in children:
                    if self._stop_event.is_set():
                        break

                    logger.debug("Monitor loop is pid enabled: %s", pid)
                    if self._enabled is not None and not self._enabled(pid):
                        logger.debug("Monitor loop is pid enabled: %s, NO", pid)
                        continue

                    logger.debug("Monitor loop is pid enabled: %s YES", pid)
                    rc = self._monitor.poll(pid)
                    logger.debug("Monitor loop is pid rc: %s, rc=%s", pid, rc)

                    wait.append(self._wait_policy.wait(rc))

                if not any(wait):
                    break

                # Wait for next polling interval
                self._stop_event.wait(timeout=self._rate)
        logger.debug("Monitor loop exit")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

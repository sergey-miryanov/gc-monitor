import threading
import time
from typing import Self

from .monitor import EventsMonitor
from .rss_sampler import RssSampler
from .run_policy import Runner
from .utils import set_on_exit

__all__ = ["MonitorLoop"]


class MonitorLoop:
    """Timing and shutdown around a monitor that owns the rest.

    One tick is one call on the monitor, which answers who was alive and
    whether any wait policy still wants the run to go on. What is left here is
    the clock, the stop event a signal handler sets, the rate, the RSS sampler
    and the break.
    """

    def __init__(
        self,
        monitor: EventsMonitor,
        runner: Runner,
        rate: float = 0.1,
        rss_sampler: RssSampler | None = None,
    ) -> None:
        self._monitor = monitor
        self._runner = runner
        self._rate = rate
        self._stop_event = threading.Event()
        self._rss_sampler = rss_sampler

    def close(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        with set_on_exit(self._stop_event):
            for _ in self._runner.run(self._stop_event.is_set):
                # One clock read per tick, in nanoseconds, and everything
                # downstream stamps and paces off this one instant.
                now_ns = time.monotonic_ns()

                report = self._monitor.tick(now_ns, self._stop_event.is_set)

                if self._rss_sampler:
                    self._rss_sampler.tick(now_ns, report.live_pids)

                if not report.keep_running:
                    break

                # Wait for next polling interval
                self._stop_event.wait(timeout=self._rate)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

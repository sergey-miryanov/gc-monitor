import threading
import time
from typing import Self

from .data import RunReport, secs_to_ns
from .monitor import EventsMonitor
from .rss_sampler import RssSampler
from .run_policy import Runner
from .utils import set_on_exit

__all__ = ["MonitorLoop"]

MIN_IDLE_NS = 1_000_000
"""The least idle gcmon leaves the target between one tick and the next.

The grid already keeps two tick starts a whole rate apart, so the only case
left to guard is a tick finishing a hair before its next position: without a
floor the loop re-enters immediately and pins gcmon at a full duty cycle
against a target that is already struggling. That window is narrow under a
grid that keeps its phase, so this is a spin-guard rather than a policy.
"""


class MonitorLoop:
    """Timing and shutdown around a monitor that owns the rest.

    One tick is one call on the monitor, which answers who was alive and
    whether any wait policy still wants the run to go on. Left here: the clock,
    the stop event a signal handler sets, the rate and the RSS sampler.

    Tick starts land on a schedule: `t0 + k * rate` for whole `k`, whatever a
    tick costs. A tick that outlasts its position skips to the next position on
    the same grid, so the phase survives and the interval degrades in whole
    multiples of the rate instead of drifting with the size of the target.
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
        # An operator types seconds; everything downstream of here is
        # nanoseconds (ADR-0009), converted back only for `Event.wait`.
        self._rate_ns = secs_to_ns(rate)
        self._stop_event = threading.Event()
        self._rss_sampler = rss_sampler

    def close(self) -> None:
        self._stop_event.set()

    def run(self) -> RunReport:
        next_ns: int | None = None
        ticks_run = 0
        ticks_skipped = 0

        with set_on_exit(self._stop_event):
            for _ in self._runner.run(self._stop_event.is_set):
                # One stamping read per tick, in nanoseconds. Everything the
                # tick emits agrees on this one instant: the monitor stamps
                # liveness with it (ADR-0011) and the sampler both paces and
                # stamps with it (ADR-0013).
                now_ns = time.monotonic_ns()

                if next_ns is None:
                    next_ns = now_ns

                ticks_run += 1
                report = self._monitor.tick(now_ns, self._stop_event.is_set)

                if self._rss_sampler:
                    self._rss_sampler.tick(now_ns, report.live_pids)

                if not report.keep_running:
                    break

                # A second read, for pacing only. It stamps nothing and reaches
                # nothing outside this method -- but the wait cannot be worked
                # out from the instant above without adding the tick's cost to
                # the interval, which is the defect ADR-0019 records. The RSS
                # round is inside the measured cost because it is inside the
                # tick.
                pacing_ns = time.monotonic_ns()

                next_ns += self._rate_ns
                while next_ns <= pacing_ns:
                    # The tick outlasted its position. Skip to the next one on
                    # the original grid rather than re-basing: missed positions
                    # are dropped, never made up.
                    next_ns += self._rate_ns
                    ticks_skipped += 1

                self._stop_event.wait(timeout=max(next_ns - pacing_ns, MIN_IDLE_NS) / 1e9)

        return RunReport(ticks_run=ticks_run, ticks_scheduled=ticks_run + ticks_skipped)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

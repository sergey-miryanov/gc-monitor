import threading
import time
from typing import Self

from .data import secs_to_ns
from .monitor import EventsMonitor
from .rss_sampler import RssSampler
from .run_policy import Runner
from .run_report import RunReport
from .utils import set_on_exit

__all__ = ["MonitorLoop"]

MIN_IDLE_NS = 1_000_000
"""The least idle gcmon leaves the target between one tick and the next.

Without it a tick finishing a hair before its next position sends the loop
straight back in, pinning gcmon at a full duty cycle against a target that is
already struggling.

It bounds the rate gcmon can hold: a rate at or below this cannot be met, and
tick starts land this far apart instead. If ``--rate`` ever grows a lower bound,
this is the number. See ADR-0019.
"""


def _next_position(position_ns: int, tick_end_ns: int, rate_ns: int) -> tuple[int, int, int]:
    """How long to idle, where the next tick starts, and what the last one missed.

    *position_ns* is the position the tick that just ended was given, and
    *tick_end_ns* the instant it ended.
    """
    assert rate_ns > 0, "a schedule needs a rate"

    missed = 0
    next_ns = position_ns + rate_ns

    if next_ns <= tick_end_ns:
        # The tick outlasted its position. Skip along the original grid
        # rather than re-basing, and count the missed positions rather
        # than stepping to them (ADR-0019).
        missed = (tick_end_ns - next_ns) // rate_ns + 1
        next_ns += missed * rate_ns

    idle_ns = next_ns - tick_end_ns

    if idle_ns < MIN_IDLE_NS:
        idle_ns = MIN_IDLE_NS
        next_ns = tick_end_ns + MIN_IDLE_NS

    return idle_ns, next_ns, missed


class MonitorLoop:
    """Timing and shutdown around a monitor that owns the rest.

    One tick is one call on the monitor, which answers who was alive and
    whether any wait policy still wants the run to go on. Left here: the clock,
    the stop event a signal handler sets, the rate and the RSS sampler.

    Tick starts land on a schedule: `t0 + k * rate` for whole `k`, whatever a
    tick costs. A tick that outlasts its position skips to the next position on
    the same grid, and the missed ones are never made up (ADR-0019).

    The rate has to be positive: `--rate` is refused before it reaches here if
    it is not, including a value too small to be a nanosecond.
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
        self._rate_ns = secs_to_ns(rate)
        if self._rate_ns <= 0:
            raise ValueError(f"rate must be a nanosecond or more, got {rate}")
        self._stop_event = threading.Event()
        self._rss_sampler = rss_sampler

    def close(self) -> None:
        self._stop_event.set()

    def run(self) -> RunReport:
        position_ns: int | None = None
        ticks_run = 0
        ticks_skipped = 0

        with set_on_exit(self._stop_event):
            for _ in self._runner.run(self._stop_event.is_set):
                tick_start_ns = time.monotonic_ns()

                if position_ns is None:
                    position_ns = tick_start_ns

                ticks_run += 1
                report = self._monitor.tick(tick_start_ns, self._stop_event.is_set)

                if self._rss_sampler:
                    self._rss_sampler.tick(tick_start_ns, report.live_pids)

                if not report.keep_running:
                    break

                tick_end_ns = time.monotonic_ns()
                idle_ns, position_ns, missed = _next_position(position_ns, tick_end_ns, self._rate_ns)
                ticks_skipped += missed

                self._stop_event.wait(timeout=idle_ns / 1e9)

        return RunReport(ticks_run=ticks_run, ticks_scheduled=ticks_run + ticks_skipped)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

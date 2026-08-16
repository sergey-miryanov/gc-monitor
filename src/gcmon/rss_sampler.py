"""RSS (Resident Set Size) sampling for monitored processes.

Why sampling lives in a class of its own, on a sentinel track, behind a flag:
ADR-0013. The loop drives it once per tick, after reporting liveness (ADR-0011).
"""

import logging
import time
from collections.abc import Callable, Set

from .exporters.exporter import EventsExporter

logger = logging.getLogger("gcmon")

__all__ = ["RssSampler"]


class RssSampler:
    """Samples RSS for live processes at a configurable interval.

    Parameters
    ----------
    exporter
        The exporter to emit RSS samples through.
    interval
        Minimum time (seconds) between RSS sampling rounds.
    rss_provider
        Optional callable returning RSS in bytes for a PID (0 if
        unreachable). Follows the same injectable-callback pattern as
        ``cmdline_provider`` in the encoder. When ``None``, defaults to
        ``_default_rss_sampler`` (psutil-based) if psutil is available,
        otherwise RSS tracking is silently disabled.
    """

    def __init__(
        self,
        exporter: EventsExporter,
        interval: float = 1.0,
        rss_provider: Callable[[int], int] | None = None,
    ) -> None:
        self._exporter = exporter
        self._interval = interval
        self._last_sample: float = 0.0
        self._enabled = True

        if rss_provider is not None:
            self._provider = rss_provider
        else:
            try:
                import psutil  # noqa: F401
            except ImportError:
                logger.info("psutil not available; RSS tracking disabled.")
                self._enabled = False
                self._provider = _noop_rss_sampler
            else:
                self._provider = _default_rss_sampler

    def tick(self, now: float, live_pids: Set[int]) -> None:
        """Sample RSS for *live_pids* if the sampling interval has elapsed."""
        if not self._enabled or not live_pids:
            return
        if now - self._last_sample < self._interval:
            return
        self._last_sample = now
        for pid in live_pids:
            self._sample(pid)

    def _sample(self, pid: int) -> None:
        try:
            rss = self._provider(pid)
        except Exception as exc:
            logger.debug("Could not sample RSS for PID %s: %s", pid, exc)
            return
        if rss:
            ts = time.monotonic_ns()
            self._exporter.add_rss_sample(pid, rss, ts)


def _noop_rss_sampler(pid: int) -> int:
    return 0


def _default_rss_sampler(pid: int) -> int:
    import psutil

    try:
        return psutil.Process(pid).memory_info().rss
    except psutil.NoSuchProcess, psutil.AccessDenied:
        return 0

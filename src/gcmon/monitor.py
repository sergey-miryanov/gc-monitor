"""Core GC monitoring functionality."""

import logging
import time
from _remote_debugging import get_child_pids, get_gc_stats
from typing import Self

from .exporters import EventsExporter
from .poll_status import PollStatus, ProcessLifecycle
from .stats import StreamingStats
from .target_process import TargetProcess

logger = logging.getLogger("gcmon")

__all__ = ["EventsMonitor", "create_monitor"]


class EventsMonitor:
    def __init__(
        self,
        process: TargetProcess,
        exporter: EventsExporter,
        stats: StreamingStats,
    ) -> None:

        self._process = process
        self._exporter = exporter
        self._enabled = True
        self._last_ts: int = 0
        self._stats = stats
        self._alive_pids: set[int] = set()
        self._reported: set[int] = set()

    def get_child_pids(self) -> list[int]:
        try:
            return get_child_pids(self._process.pid, recursive=True)
        except Exception as exc:
            logger.warning(
                "Monitor for PID %s encountered error while gathering children PIDs", self._process.pid, exc_info=exc
            )
            return []

    def _mark_started(self, pid: int) -> None:
        if pid in self._alive_pids:
            return
        logger.debug(f"Mark PID alive: {pid=}")
        self._alive_pids.add(pid)
        self._exporter.mark_process_lifecycle(
            pid, ProcessLifecycle.STARTED, time.monotonic_ns(),
        )

    def _mark_died(self, pid: int) -> None:
        if pid not in self._alive_pids:
            return
        logger.debug(f"Mark PID died: {pid=}")
        self._alive_pids.discard(pid)
        self._exporter.mark_process_lifecycle(
            pid, ProcessLifecycle.DIED, time.monotonic_ns(),
        )

    def mark_pid_died(self, pid: int) -> bool:
        """Externally report that *pid* is no longer alive.

        Emits a ``ProcessLifecycle.DIED`` transition for *pid* if the
        monitor previously reported it as ``STARTED`` (i.e. the pid
        appeared in a successful ``poll()``). Returns ``True`` when a
        transition was emitted, ``False`` otherwise.

        This is the hook the ``MonitorLoop`` uses to detect a child
        process that disappeared from the parent's child list between
        poll cycles, so its lifetime slice can be closed even when
        ``poll()`` is never called for the dead pid.
        """
        if pid not in self._alive_pids:
            return False
        self._alive_pids.discard(pid)
        logger.debug(f"Mark PID died (external): {pid=}")
        self._exporter.mark_process_lifecycle(
            pid, ProcessLifecycle.DIED, time.monotonic_ns(),
        )
        return True

    def poll(self, pid: int) -> PollStatus:

        if not self._enabled:
            logger.warning(
                "Monitor for PID %s already stopped",
                pid,
            )
            return PollStatus.FAIL

        try:
            events = get_gc_stats(pid, all_interpreters=True)
            self._mark_started(pid)
            for event in events:
                # Skip events with timestamps already processed
                if event.ts_start > self._last_ts and event.ts_start < event.ts_stop:
                    self._exporter.add_event(pid, event)
                    self._stats.update(pid, event)
                    self._last_ts = event.ts_start

            return PollStatus.OK
        except RuntimeError as exc:
            self._mark_died(pid)
            if pid not in self._reported:
                logger.debug("Error while polling PID %s (child PID=%s): %s", self._process.pid, pid, exc)
                self._reported.add(pid)
            return PollStatus.INVALID_PROCESS
        except PermissionError as exc:
            self._mark_died(pid)
            if pid not in self._reported:
                logger.debug("Error while polling PID %s (child PID=%s): %s", self._process.pid, pid, exc)
                self._reported.add(pid)
            return PollStatus.INVALID_PROCESS
        except Exception as exc:
            logger.warning("Monitor for PID %s (child PID=%s) encountered error", self._process.pid, pid, exc_info=exc)
            return PollStatus.FAIL

    def stop(self) -> None:
        """Stop monitoring and close the handler and exporter.

        Safe to call multiple times. Emits a ``ProcessLifecycle.DIED``
        transition for every pid that was previously reported as
        ``STARTED`` so the exporter can close out the per-process
        lifetime slice with a final timestamp.
        """
        try:
            for pid in list(self._alive_pids):
                try:
                    self._exporter.mark_process_lifecycle(
                        pid, ProcessLifecycle.DIED, time.monotonic_ns(),
                    )
                except Exception as exc:
                    logger.warning(
                        "Monitor for PID %s failed to emit DIED for child PID %s on stop: %s",
                        self._process.pid, pid, exc,
                    )
            self._alive_pids.clear()
        finally:
            self._exporter.close()
            self._enabled = False

    @property
    def is_enabled(self) -> bool:
        """Check if monitor is currently enabled."""
        return self._enabled

    @property
    def pid(self) -> int:
        """Return the process ID being monitored."""
        return self._process.pid

    @property
    def exporter(self) -> EventsExporter:
        return self._exporter

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()


def create_monitor(
    process: TargetProcess,
    exporter: EventsExporter,
    stats: StreamingStats,
) -> EventsMonitor:
    """Create a GCMonitor for the given process.

    Args:
        process: Target process to monitor.
        exporter: Events exporter.

    Returns:
        A GCMonitor instance ready to be added to a GCMonitorThread.
    """
    return EventsMonitor(process, exporter, stats)

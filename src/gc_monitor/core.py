"""Core GC monitoring functionality."""

import sys
import threading
import time
from typing import Callable, Optional

from ._gc_monitor import GCMonitorHandler, GCMonitorStatsItem, connect as _connect


class GCMonitor:
    """GC event monitor that polls at a fixed rate."""

    def __init__(
        self,
        handler: GCMonitorHandler,
        callback: Callable[[GCMonitorStatsItem], None],
        rate: float = 0.1,
    ) -> None:
        self._handler = handler
        self._callback = callback
        self._rate = rate
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._handler.close()

    def _run(self) -> None:
        """Background thread: poll for events and call callback."""
        while self._running:
            try:
                events = self._handler.read()
                for event in events:
                    self._callback(event)
            except RuntimeError:
                break
            time.sleep(self._rate)


def connect(
    pid: int, callback: Callable[[GCMonitorStatsItem], None], rate: float = 0.1
) -> Optional[GCMonitor]:
    """
    Connect to GC monitor for the given process and start monitoring.

    Args:
        pid: Process ID to monitor
        callback: Function called for each GC event
        rate: Polling interval in seconds (default: 0.1)

    Returns:
        GCMonitor instance on success, None on failure

    Note:
        Prints exception message to stderr on failure.
    """
    try:
        handler = _connect(pid)
        monitor = GCMonitor(handler, callback, rate)
        return monitor
    except RuntimeError as e:
        print(f"Failed to connect to GC monitor for PID {pid}: {e}", file=sys.stderr)
        return None

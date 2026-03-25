"""Core GC monitoring functionality."""

import threading
import warnings

# Try to import from experimental CPython _gc_monitor module first,
# fall back to mock implementation if not available (controlled by use_fallback parameter)
_gc_monitor_available = False
try:
    from _gc_monitor import connect as _connect  # type: ignore[import-not-found]
    _gc_monitor_available = True
except ImportError:
    # Real _gc_monitor module not available, will use mock if fallback enabled
    from .fallback import connect as _connect

from .exporter import GCMonitorExporter
from .protocol import MonitorHandler

__all__ = ["GCMonitor", "connect"]


class GCMonitor:
    """GC event monitor that polls at a fixed rate.

    Automatically stops when the target process terminates.
    Uses threading.Event for responsive shutdown signaling.
    """

    def __init__(
        self,
        handler: MonitorHandler,
        exporter: GCMonitorExporter,
        rate: float = 0.1,
    ) -> None:
        self._handler = handler
        self._exporter = exporter
        self._rate = rate
        self._stopped = False
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop monitoring and close the exporter.

        Signals the monitoring thread to stop and waits for it to finish.
        Safe to call multiple times.
        """
        if self._stopped:
            # Already stopped, but still close exporter to flush data
            self._exporter.close()
            return
        # Signal the monitoring thread to stop
        self._stop_event.set()
        # Wait for the thread to finish (with timeout)
        self._thread.join(timeout=1.0)
        self._handler.close()
        self._exporter.close()
        self._stopped = True

    @property
    def is_running(self) -> bool:
        """Check if monitor is still running."""
        return not self._stopped and not self._stop_event.is_set()

    def _run(self) -> None:
        """Background thread: poll for events and export events.

        Stops automatically if the target process terminates (RuntimeError from handler)
        or when stop_event is set.
        Skips events with timestamps that were already processed to avoid duplicates.
        """
        last_ts: int = 0
        while not self._stop_event.is_set():
            try:
                events = self._handler.read()
                for event in events:
                    # Skip events with timestamps already processed
                    if event.ts > last_ts:
                        self._exporter.add_event(event)
                        last_ts = event.ts
            except RuntimeError:
                # Target process terminated or handler error - stop gracefully
                break
            # Use wait() instead of sleep() for responsive shutdown
            # wait() returns immediately if stop_event is set
            self._stop_event.wait(timeout=self._rate)
        self._stopped = True


def connect(
    pid: int, exporter: "GCMonitorExporter", rate: float = 0.1, use_fallback: bool = True
) -> GCMonitor:
    """
    Connect to GC monitor for the given process and start monitoring.

    Args:
        pid: Process ID to monitor
        exporter: Exporter to collect and save events
        rate: Polling interval in seconds (default: 0.1)
        use_fallback: Use mock implementation if _gc_monitor not available (default: True)

    Returns:
        GCMonitor instance on success

    Raises:
        RuntimeError: If connection fails or if _gc_monitor module is not available and use_fallback=False.
    """
    # Check if we need to use fallback and if it's allowed
    if not _gc_monitor_available and not use_fallback:
        error_msg = (
            "Experimental CPython _gc_monitor module not available. "
            "Use --fallback=yes to use mock implementation."
        )
        raise RuntimeError(error_msg)

    # Show warning when using fallback
    if not _gc_monitor_available and use_fallback:
        warning_msg = (
            "Experimental CPython _gc_monitor module not available. "
            "Using mock implementation from gc_monitor._gc_monitor. "
            "GC monitoring will use simulated data. "
            "Use --fallback=no to disable fallback and raise an error instead."
        )
        warnings.warn(warning_msg, RuntimeWarning, stacklevel=2)

    handler = _connect(pid)
    monitor = GCMonitor(handler, exporter, rate)
    return monitor

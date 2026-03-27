"""Core GC monitoring functionality."""

import logging
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
from .monitor_thread import GCMonitorThread
from .protocol import MonitorHandler

logger = logging.getLogger("gc_monitor.monitor")

__all__ = ["GCMonitor", "GCMonitorThread", "connect"]


class GCMonitor:
    """GC event monitor that acts as a bridge between handler and exporter.

    Polls a MonitorHandler for GC events and exports them via GCMonitorExporter.
    Designed to be managed by GCMonitorThread for cooperative multi-monitor scheduling.

    Example:
        ```python
        # Create handler and exporter
        handler = connect(pid)
        exporter = TraceExporter(pid=pid, output_path=Path("trace.json"))

        # Create monitor (no thread started yet)
        monitor = GCMonitor(handler, exporter, rate=0.1)

        # Option 1: Use with GCMonitorThread (recommended for multiple monitors)
        thread = GCMonitorThread(rate=0.1)
        thread.add_monitor(monitor)
        thread.start()

        # Option 2: Use with convenience connect() function (single monitor)
        monitor, thread = connect(pid, exporter, rate=0.1)
        ```
    """

    def __init__(
        self,
        handler: MonitorHandler,
        exporter: GCMonitorExporter
    ) -> None:
        """Initialize the GC monitor.

        Args:
            handler: MonitorHandler to read GC events from
            exporter: GCMonitorExporter to write GC events to
            rate: Polling interval in seconds (default: 0.1)
        """
        self._handler = handler
        self._exporter = exporter
        self._enabled = True
        self._last_ts: int = 0

    def poll(self) -> bool:
        """Perform a single polling iteration.

        Reads events from the handler and exports them.
        Skips events with timestamps already processed to avoid duplicates.

        Returns:
            True if monitoring can continue, False if should stop

        Raises:
            RuntimeError: If the target process has terminated or handler error
        """
        assert self._enabled

        try:
            events = self._handler.read()
            for event in events:
                # Skip events with timestamps already processed
                if event.ts > self._last_ts:
                    self._exporter.add_event(event)
                    self._last_ts = event.ts
            return True
        except RuntimeError:
            self._enabled =  False
            return False
        except Exception as exc:
            logger.warning(
                "Monitor for PID %s encountered error, disabling",
                self.pid,
                exc_info=exc
            )
            # Target process terminated or handler error
            self._enabled = False
            return False

    def stop(self) -> None:
        """Stop monitoring and close the handler and exporter.

        Safe to call multiple times.
        """
        if self._enabled:
            self._handler.close()
            self._exporter.close()
            self._enabled = False

        self._exporter.close()

    @property
    def is_enabled(self) -> bool:
        """Check if monitor is currently enabled."""
        return self._enabled

    @property
    def pid(self) -> int:
        """Return the process ID being monitored.

        Extracted from the exporter's pid property.
        """
        return self._exporter.pid


def connect(
    pid: int,
    exporter: GCMonitorExporter,
    use_fallback: bool = True,
) -> GCMonitor:
    """
    Connect to GC monitor for the given process.

    Creates a GCMonitor instance that can be used with GCMonitorThread
    for cooperative multi-monitor scheduling.

    Args:
        pid: Process ID to monitor
        exporter: Exporter to collect and save events
        rate: Polling interval in seconds (default: 0.1)
        use_fallback: Use mock implementation if _gc_monitor not available (default: True)

    Returns:
        GCMonitor instance on success

    Raises:
        RuntimeError: If connection fails or if _gc_monitor module is not available and use_fallback=False.

    Example:
        ```python
        exporter = TraceExporter(pid=12345, output_path=Path("trace.json"))
        monitor = connect(12345, exporter, rate=0.1)

        # Use with GCMonitorThread
        thread = GCMonitorThread(rate=0.1)
        thread.add_monitor(monitor)
        thread.start()

        # Monitor for 5 seconds
        time.sleep(5)

        # Stop monitoring
        thread.stop()
        ```
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
    monitor = GCMonitor(handler, exporter)
    return monitor

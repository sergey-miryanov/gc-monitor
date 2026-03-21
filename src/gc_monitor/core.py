"""Core GC monitoring functionality."""

import sys
import threading
import time
from typing import TYPE_CHECKING, Optional

# Try to import from experimental CPython _gc_monitor module first,
# fall back to mock implementation if not available
if TYPE_CHECKING:
    # For type checking, always use the stub/mock types
    from ._gc_monitor import GCMonitorHandler, connect as _connect
else:
    # At runtime, try the real module first
    try:
        from _gc_monitor import GCMonitorHandler, connect as _connect  # type: ignore[import-not-found]
    except ImportError:
        from ._gc_monitor import GCMonitorHandler, connect as _connect

if TYPE_CHECKING:
    from .exporter import GCMonitorExporter


class GCMonitor:
    """GC event monitor that polls at a fixed rate."""

    def __init__(
        self,
        handler: GCMonitorHandler,
        exporter: "GCMonitorExporter",
        rate: float = 0.1,
    ) -> None:
        self._handler = handler
        self._exporter = exporter
        self._rate = rate
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop monitoring and close the exporter."""
        self._running = False
        self._thread.join(timeout=1.0)
        self._handler.close()
        self._exporter.close()

    def _run(self) -> None:
        """Background thread: poll for events and export events."""
        while self._running:
            try:
                events = self._handler.read()
                for event in events:
                    self._exporter.add_event(event)
            except RuntimeError:
                break
            time.sleep(self._rate)


def connect(
    pid: int, exporter: "GCMonitorExporter", rate: float = 0.1
) -> Optional[GCMonitor]:
    """
    Connect to GC monitor for the given process and start monitoring.

    Args:
        pid: Process ID to monitor
        exporter: Exporter to collect and save events
        rate: Polling interval in seconds (default: 0.1)

    Returns:
        GCMonitor instance on success, None on failure

    Note:
        Prints exception message to stderr on failure.
    """
    try:
        handler = _connect(pid)
        monitor = GCMonitor(handler, exporter, rate)
        return monitor
    except RuntimeError as e:
        print(f"Failed to connect to GC monitor for PID {pid}: {e}", file=sys.stderr)
        return None

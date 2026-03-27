"""Thread manager for running multiple GCMonitor instances cooperatively."""

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import GCMonitor

logger = logging.getLogger("gc_monitor.monitor_thread")

__all__ = ["GCMonitorThread"]


class GCMonitorThread:
    """Manages multiple GCMonitor instances in a single background thread.

    Runs an event loop that polls all registered monitors at a fixed rate.
    Supports dynamic addition and removal of monitors during runtime.
    Uses a threading.Event for responsive shutdown signaling.

    Example:
        ```python
        # Create thread manager
        thread = GCMonitorThread(rate=0.1)

        # Create monitors
        monitor1 = GCMonitor(handler1, exporter1, rate=0.1)
        monitor2 = GCMonitor(handler2, exporter2, rate=0.1)

        # Add monitors to thread
        thread.add_monitor(monitor1)
        thread.add_monitor(monitor2)

        # Start monitoring
        thread.start()

        # ... wait for some time ...

        # Stop all monitors
        thread.stop()
        ```
    """

    def __init__(self, rate: float = 0.1, stop_if_empty:bool=True) -> None:
        """Initialize the monitor thread.

        Args:
            rate: Polling interval in seconds for all monitors (default: 0.1)
        """
        self._rate = rate
        self._monitors: list[GCMonitor] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._stop_if_empty = stop_if_empty
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        """Start the background thread.

        Safe to call only once. Raises RuntimeError if thread is already started.
        """
        if self._thread.is_alive():
            raise RuntimeError("GCMonitorThread is already running")
        self._stop_event.clear()
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        """Stop the background thread and all monitors.

        Signals all monitors to stop, waits for the thread to finish,
        and closes all handlers and exporters.

        Args:
            timeout: Maximum time to wait for thread to stop (default: 1.0 seconds)
        """
        # Signal all monitors to stop
        self._stop_event.set()

        # Wait for thread to finish
        self._thread.join(timeout=timeout)

        # Close all monitors (handlers and exporters)
        with self._lock:
            for monitor in self._monitors:
                monitor.stop()
            self._monitors.clear()

    def add_monitor(self, monitor: "GCMonitor") -> None:
        """Add a monitor to be managed by this thread.

        Args:
            monitor: GCMonitor instance to add

        Note:
            Monitors can be added dynamically while the thread is running.
        """
        with self._lock:
            if monitor not in self._monitors:
                self._monitors.append(monitor)

    def remove_monitor(self, monitor: "GCMonitor") -> bool:
        """Remove a monitor from management.

        Args:
            monitor: GCMonitor instance to remove

        Returns:
            True if monitor was removed, False if it was not in the list

        Note:
            The monitor is stopped (handler and exporter closed) when removed.
        """
        with self._lock:
            if monitor in self._monitors:
                self._monitors.remove(monitor)
                monitor.stop()
                return True
        return False

    @property
    def is_running(self) -> bool:
        """Check if the thread is currently running."""
        return self._thread.is_alive() and not self._stop_event.is_set()

    @property
    def monitor_count(self) -> int:
        """Return the number of registered monitors."""
        with self._lock:
            return len(self._monitors)

    def _run(self) -> None:
        """Background thread event loop.

        Polls all registered monitors in round-robin fashion.
        Stops when stop_event is set or when all monitors have stopped.
        """
        while not self._stop_event.is_set():
            try:
                # Get a snapshot of monitors to iterate over
                with self._lock:
                    monitors_snapshot = list(self._monitors)

                if not monitors_snapshot:
                    # No monitors to poll, wait and check again
                    self._stop_event.wait(timeout=self._rate)
                    continue

                active_monitors = 0
                for monitor in monitors_snapshot:
                    if self._stop_event.is_set():
                        break

                    # Poll the monitor
                    if not monitor.poll():
                        self.remove_monitor(monitor)
                    else:
                        active_monitors += 1

                if self._stop_if_empty and active_monitors == 0:
                    # All monitors have stopped, exit loop
                    break

                # Wait for next polling interval
                self._stop_event.wait(timeout=self._rate)
            except Exception as exc:
                logger.warning(
                    "Monitor thread has exception",
                    exc_info=exc
                )
                self.stop()

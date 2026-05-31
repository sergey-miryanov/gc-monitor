import logging
import threading

from .monitor import EventsMonitor
from .wait_policy import WaitPolicy, WaitPolicyFactory

logger = logging.getLogger("gc_monitor")

__all__ = ["MonitorThread"]


class MonitorThread:
    def __init__(
        self,
        wait_policy_factory: WaitPolicyFactory,
        rate: float = 0.1,
    ) -> None:
        self._wait_policy_factory = wait_policy_factory
        self._rate = rate
        self._monitors: dict[EventsMonitor, WaitPolicy] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
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
        self._stop_monitors()

    def close(self) -> None:
        self.stop()

    def _stop_monitors(self) -> None:
        with self._lock:
            for monitor in self._monitors:
                monitor.stop()
            self._monitors.clear()

    def add_monitor(self, monitor: EventsMonitor) -> None:
        """Add a monitor to be managed by this thread.

        Args:
            monitor: GCMonitor instance to add

        Note:
            Monitors can be added dynamically while the thread is running.
        """
        with self._lock:
            if monitor not in self._monitors:
                policy = self._wait_policy_factory()
                self._monitors[monitor] = policy

    def remove_monitor(self, monitor: EventsMonitor) -> bool:
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
                self._monitors.pop(monitor)
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
                    monitors_snapshot = {**self._monitors}

                if not monitors_snapshot:
                    # No monitors to poll, wait and check again
                    self._stop_event.wait(timeout=self._rate)
                    continue

                for monitor, wait_policy in monitors_snapshot.items():
                    if self._stop_event.is_set():
                        break

                    # Poll the monitor
                    rc = monitor.poll(monitor.pid)
                    if not wait_policy.wait(rc):
                        self.remove_monitor(monitor)

                # Wait for next polling interval
                self._stop_event.wait(timeout=self._rate)
            except Exception:
                logger.error("Monitor thread encountered unexpected error", exc_info=True)
                self._stop_monitors()
                self._stop_event.set()

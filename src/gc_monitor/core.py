"""Core GC monitoring functionality."""

import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ._gc_monitor import GCMonitorHandler, connect as _connect

if TYPE_CHECKING:
    from .exporter import GCMonitorExporter as ExporterType


class GCMonitor:
    """GC event monitor that polls at a fixed rate."""

    def __init__(
        self,
        handler: GCMonitorHandler,
        exporter: ExporterType,
        rate: float = 0.1,
    ) -> None:
        self._handler = handler
        self._exporter = exporter
        self._rate = rate
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, save_path: Optional[Path] = None) -> None:
        """Stop monitoring and optionally save trace to file."""
        self._running = False
        self._thread.join(timeout=1.0)
        self._handler.close()
        if save_path is not None:
            self._exporter.save_json(save_path)

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
    pid: int, exporter: ExporterType, rate: float = 0.1
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

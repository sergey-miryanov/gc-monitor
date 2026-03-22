
"""Mock GC monitor module for testing when CPython _gc_monitor is not available.

This module provides a mock implementation of the _gc_monitor C extension module
for testing and development purposes. When running on CPython with the experimental
_gc_monitor module available, the real implementation will be used instead.
"""

import random

__all__ = [
    "GCMonitorStatsItem",
    "GCMonitorHandler",
    "connect",
    "disconnect",
]


class GCMonitorStatsItem:
    """
    GC monitoring statistics item.

    Attributes:
        gen: GC generation (0, 1, or 2)
        ts: Timestamp in nanoseconds
        collections: Number of collections
        collected: Number of objects collected
        uncollectable: Number of uncollectable objects
        candidates: Number of candidate objects
        object_visits: Number of object visits
        objects_transitively_reachable: Objects transitively reachable
        objects_not_transitively_reachable: Objects not transitively reachable
        heap_size: Heap size in bytes
        work_to_do: Work to do metric
        duration: GC pause duration in seconds
        total_duration: Total GC duration in seconds
    """

    def __init__(
        self,
        gen: int,
        ts: int,
        collections: int,
        collected: int,
        uncollectable: int,
        candidates: int,
        object_visits: int,
        objects_transitively_reachable: int,
        objects_not_transitively_reachable: int,
        heap_size: int,
        work_to_do: int,
        duration: float,
        total_duration: float,
    ) -> None:
        self.gen: int = gen
        self.ts: int = ts  # Timestamp in nanoseconds
        self.collections: int = collections
        self.collected: int = collected
        self.uncollectable: int = uncollectable
        self.candidates: int = candidates
        self.object_visits: int = object_visits
        self.objects_transitively_reachable: int = objects_transitively_reachable
        self.objects_not_transitively_reachable: int = objects_not_transitively_reachable
        self.heap_size: int = heap_size
        self.work_to_do: int = work_to_do
        self.duration: float = duration  # Duration in seconds
        self.total_duration: float = total_duration  # Duration in seconds


class GCMonitorHandler:
    """Mock GC monitor handler for testing."""

    def __init__(self) -> None:
        self._connected = True
        self._length = random.randint(1, 10)

    def read(self) -> list[GCMonitorStatsItem]:
        """Read GC monitoring events.

        Returns:
            List of GCMonitorStatsItem instances.

        Raises:
            RuntimeError: If handler is not connected or read fails.
        """
        if not self._connected:
            raise RuntimeError("Handler is not connected")
        # Simulate potential read failure (terminal - handler broken after this)
        if random.random() < 0.1:
            self._connected = False
            raise RuntimeError("Read failed - connection broken")
        # Generate base timestamp in nanoseconds (e.g., current time in ns)
        base_ts = 1_000_000_000  # 1 second in nanoseconds
        return [
            GCMonitorStatsItem(
                gen=random.randint(0, 2),
                ts=base_ts + (i * 100_000_000),  # 100ms apart in nanoseconds
                collections=random.randint(1, 100),
                collected=random.randint(10, 100),
                uncollectable=random.randint(0, 10),
                candidates=random.randint(5, 50),
                object_visits=random.randint(100, 1000),
                objects_transitively_reachable=random.randint(50, 500),
                objects_not_transitively_reachable=random.randint(50, 500),
                heap_size=random.randint(10000, 100000),
                work_to_do=random.randint(0, 100),
                duration=random.uniform(0.001, 0.010),  # 1-10ms in seconds
                total_duration=random.uniform(1.0, 50.0),
            )
            for i in range(self._length)
        ]

    def close(self) -> None:
        """Close the handler."""
        self._connected = False

    def __enter__(self) -> "GCMonitorHandler":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        self.close()


def connect(pid: int) -> GCMonitorHandler:
    """Connect to GC monitor for the given process.

    Args:
        pid: Process ID to monitor.

    Returns:
        GCMonitorHandler instance.
    """
    return GCMonitorHandler()


def disconnect(handler: GCMonitorHandler) -> None:
    """Disconnect from GC monitor.

    Args:
        handler: GCMonitorHandler instance to disconnect.
    """
    handler.close()

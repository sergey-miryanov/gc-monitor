"""Type stubs for experimental CPython _gc_monitor module.

This stub file provides type information for the _gc_monitor C extension module
when it is available. When the module is not available, the mock implementation
in _gc_monitor.py is used instead.
"""

from typing import List

__all__ = [
    "GCMonitorStatsItem",
    "GCMonitorHandler",
    "connect",
    "disconnect",
]


class GCMonitorStatsItem:
    """GC monitoring statistics item."""

    gen: int
    ts: int  # Timestamp in nanoseconds
    collections: int
    collected: int
    uncollectable: int
    candidates: int
    object_visits: int
    objects_transitively_reachable: int
    objects_not_transitively_reachable: int
    heap_size: int
    work_to_do: int
    duration: float  # GC pause duration in seconds
    total_duration: float  # Total GC duration in seconds

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
    ) -> None: ...


class GCMonitorHandler:
    """GC monitor handler."""

    _connected: bool

    def __init__(self) -> None: ...
    def read(self) -> List[GCMonitorStatsItem]: ...
    def close(self) -> None: ...
    def __enter__(self) -> "GCMonitorHandler": ...
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None: ...


def connect(pid: int) -> GCMonitorHandler: ...
def disconnect(handler: GCMonitorHandler) -> None: ...

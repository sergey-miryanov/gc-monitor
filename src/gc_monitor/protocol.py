from typing import Protocol, runtime_checkable
from collections.abc import Sequence


@runtime_checkable
class StatsItem(Protocol):
    """Protocol for GC statistics items."""

    gen: int
    ts: int
    collections: int
    collected: int
    uncollectable: int
    candidates: int
    object_visits: int
    objects_transitively_reachable: int
    objects_not_transitively_reachable: int
    heap_size: int
    work_to_do: int
    duration: float
    total_duration: float


@runtime_checkable
class MonitorHandler(Protocol):
    """Protocol for GC monitor handlers."""

    def read(self) -> Sequence[StatsItem]: ...

    def close(self) -> None: ...

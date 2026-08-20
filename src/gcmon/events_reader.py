"""Reaching a process: one attachment per pid, held across many reads.

Read ADR-0020 for more.
"""

from _remote_debugging import GCMonitor
from collections.abc import Sequence, Set
from typing import Protocol, override, runtime_checkable

from .protocol import TGCStatsInfo

__all__ = ["EventsReader", "RemoteEventsReader", "TargetUnavailable"]


class TargetUnavailable(Exception):
    """gcmon cannot read this process right now.

    It has not started yet, it has exited, gcmon may not look at it, or its GC
    layout does not match the interpreter gcmon is running on. ADR-0020 says
    why gcmon does not tell them apart.
    """


@runtime_checkable
class EventsReader(Protocol):
    """Reads GC records per pid."""

    def read(self, pid: int) -> Sequence[TGCStatsInfo]:
        """Every record the target's rings currently hold.

        Raises :class:`TargetUnavailable` when the process cannot be read.
        """
        ...

    def retain(self, pids: Set[int]) -> None:
        """Let go of every pid outside *pids*, all of it at once."""
        ...

    def forget(self, pid: int) -> None:
        """Let go of *pid*. A no-op for one that was never read."""
        ...


class RemoteEventsReader(EventsReader):
    """An :class:`EventsReader` backed by ``_remote_debugging.GCMonitor``."""

    def __init__(self) -> None:
        self._monitors: dict[int, GCMonitor] = {}

    @override
    def read(self, pid: int) -> Sequence[TGCStatsInfo]:
        # Popping up front is what makes ADR-0020's lifetime hold for *any*
        # failure and not only for the ones translated below: nothing is put
        # back unless the read returned. So a failed attach is never
        # remembered, and a failed read lets go of the attachment it had.
        monitor = self._monitors.pop(pid, None)
        try:
            if monitor is None:
                # debug=True selects the exception *type* CPython raises, not a
                # log level; the free get_gc_stats function hardcoded it, so
                # gcmon catches and logs what it always did.
                monitor = GCMonitor(pid, debug=True)
            records = monitor.get_gc_stats(all_interpreters=True)
        except (RuntimeError, OSError) as exc:
            # RuntimeError is what debug=True turns every failure into.
            # OSError covers what reaches gcmon without passing through that
            # macro: ESRCH for a dead target, EPERM for one gcmon may not read.
            raise TargetUnavailable(f"PID {pid} is not readable: {exc}") from exc

        self._monitors[pid] = monitor
        return records

    @override
    def retain(self, pids: Set[int]) -> None:
        for pid in self._monitors.keys() - pids:
            del self._monitors[pid]

    @override
    def forget(self, pid: int) -> None:
        self._monitors.pop(pid, None)

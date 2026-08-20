"""Reaching a process: one attachment per pid, held across many reads.

Finding a target costs roughly two orders of magnitude more than reading it, so
gcmon attaches once and reads many times. The pid becomes something gcmon
*holds* rather than an argument it passes, and [ADR-0020] fixes the lifetime of
what it holds.

This is the only module in the package that imports a stateful handle from
``_remote_debugging``, and the only one that names its exception types. Callers
see :class:`TargetUnavailable` and nothing else about the platform underneath.

[ADR-0020]: ../../docs/adr/0020-attach-to-a-process-once.md
"""

from _remote_debugging import GCMonitor
from collections.abc import Sequence, Set
from typing import Protocol, override, runtime_checkable

from .protocol import TGCStatsInfo

__all__ = ["EventsReader", "RemoteEventsReader", "TargetUnavailable"]


class TargetUnavailable(Exception):
    """gcmon cannot read this process right now.

    It has not started yet, it has exited, gcmon may not look at it, or its GC
    layout does not match the interpreter gcmon is running on. Every one of
    those means the same thing to a wait policy, and ADR-0020 says why telling
    them apart is not worth what it costs yet.
    """


@runtime_checkable
class EventsReader(Protocol):
    """Reads one process's GC records, and holds whatever that takes.

    ``retain`` and ``forget`` are the pruning half. A reader is per-pid state,
    so ADR-0017's rule applies to it: :class:`~gcmon.monitor.EventsMonitor`
    owns it, prunes it in the one pass that prunes cursors and statistics, and
    nothing prunes it anywhere else.
    """

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
    """An :class:`EventsReader` backed by ``_remote_debugging.GCMonitor``.

    Attaches lazily, on the first read of a pid, and holds the attachment until
    a read fails or the monitor prunes it. Not safe to share between threads,
    which is the same constraint the monitor it serves carries.
    """

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
                # log level; the free function this replaced hardcoded it, so
                # gcmon catches and logs what it always did. ADR-0020.
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

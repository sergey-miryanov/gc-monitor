"""Which process a record belongs to, which a pid does not say."""

from typing import Protocol

import msgspec

__all__ = ["Process", "ProcessLookup"]


class Process(msgspec.Struct, frozen=True, eq=False):
    """One process gcmon monitored.

    ``pid_epoch`` counts the processes that have held *pid*, from 1;
    `ProcessRegistry` is what assigns it (ADR-0025).

    ``start_ts`` is when the monitor discovered the process, not the
    earliest evidence of it: a poll returns collections that already
    happened, so an event may carry a smaller timestamp.
    """

    pid: int
    pid_epoch: int
    start_ts: int
    cmdline: tuple[str, ...] | None = None

    def __eq__(self, other: object) -> bool:
        """Two of these are the same process when they name the same one."""
        if not isinstance(other, Process):
            return NotImplemented
        return (self.pid, self.pid_epoch) == (other.pid, other.pid_epoch)

    def __ne__(self, other: object) -> bool:
        # msgspec keeps a non-negating `__ne__` on the class when `eq=False`.
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self) -> int:
        return hash((self.pid, self.pid_epoch))

    def __lt__(self, other: Process) -> bool:
        return (self.pid, self.pid_epoch) < (other.pid, other.pid_epoch)

    @property
    def epoch_suffix(self) -> str:
        """``#2`` for the second process to hold the pid, empty for the first."""
        return "" if self.pid_epoch == 1 else f"#{self.pid_epoch}"

    def __str__(self) -> str:
        return f"{self.pid}{self.epoch_suffix}"


class ProcessLookup(Protocol):
    """What a reader of the process registry may ask of it.

    `ProcessRegistry` in `monitoring` implements it (ADR-0025).
    """

    def at(self, pid: int, ts: int) -> Process | None:
        """The process that held *pid* at *ts*, or ``None`` where none did."""
        ...

"""Which process a record belongs to, which a pid does not say."""

from typing import Protocol

import msgspec

__all__ = ["Process", "ProcessLookup"]


class Process(msgspec.Struct, frozen=True, order=True):
    """One process gcmon monitored.

    ``pid_epoch`` counts the processes that have held *pid*, from 1;
    `ProcessRegistry` is what assigns it (ADR-0025).
    """

    pid: int
    pid_epoch: int

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

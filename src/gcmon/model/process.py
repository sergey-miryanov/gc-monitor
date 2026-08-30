"""Which process a record belongs to, which a pid does not say."""

from typing import Protocol

import msgspec

__all__ = ["Process", "ProcessLookup"]


class Process(msgspec.Struct, frozen=True, eq=False):
    """One process gcmon monitored.

    ``pid_epoch`` counts the processes that have held *pid*, from 1, so a
    pid the operating system handed out twice is two of these and a
    successor's figures never land on its predecessor (ADR-0025).

    ``start_ts`` is when the monitor discovered the process, not the
    earliest evidence of it: a poll returns collections that already
    happened, so an event may carry a smaller timestamp.

    Only `ProcessRegistry` mints one.
    """

    pid: int
    pid_epoch: int
    start_ts: int
    cmdline: tuple[str, ...] | None = None

    def __eq__(self, other: object) -> bool:
        """Two of these are the same process when they name the same one.

        ``start_ts`` and ``cmdline`` are what gcmon learned about the
        process rather than which process it is, so they stay out of this
        and out of the hash. A caller holding a pid and an epoch can reach
        the rings filed under it without reproducing what gcmon read.
        """
        if not isinstance(other, Process):
            return NotImplemented
        return (self.pid, self.pid_epoch) == (other.pid, other.pid_epoch)

    def __ne__(self, other: object) -> bool:
        # Written out because msgspec's own `__ne__` stays on the class when
        # `eq=False`, and it does not negate the one above: without this, two
        # processes compare both equal and unequal.
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __hash__(self) -> int:
        return hash((self.pid, self.pid_epoch))

    def __lt__(self, other: Process) -> bool:
        """By pid, then epoch, so a run's keys sort the way the `--stats`
        table prints them."""
        return (self.pid, self.pid_epoch) < (other.pid, other.pid_epoch)

    @property
    def epoch_suffix(self) -> str:
        """``#2`` for the second process to hold the pid, empty for the first.

        The piece rather than the whole label: the `--stats` table writes it
        after the interpreter, `12345:0#2`, and a caller naming the process
        alone writes it after the pid.
        """
        return "" if self.pid_epoch == 1 else f"#{self.pid_epoch}"

    def __str__(self) -> str:
        return f"{self.pid}{self.epoch_suffix}"


class ProcessLookup(Protocol):
    """What a reader of the process registry may ask of it.

    Minting is the monitor's alone, so a layer that only has to say which
    process some evidence belongs to is handed this rather than the
    registry (ADR-0025). `ProcessRegistry` is the implementation, and it
    lives in `monitoring` because that is who writes to it.
    """

    def at(self, pid: int, ts: int) -> Process | None:
        """The process that held *pid* at *ts*, or ``None`` where none did."""
        ...

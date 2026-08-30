"""Which process holds a pid now, and which ones held it before."""

import logging
import threading
from collections.abc import Callable, Set

from ..model.process import Process

__all__ = ["CmdlineProvider", "ProcessRegistry", "read_cmdline"]

logger = logging.getLogger("gcmon")

type CmdlineProvider = Callable[[int], tuple[str, ...] | None]


def read_cmdline(pid: int) -> tuple[str, ...] | None:
    """What *pid* is running, off psutil.

    Wired in by the caller rather than defaulted to here, so a test naming
    a pid this machine does not have reads nothing instead of whatever
    happens to hold that number.
    """
    import psutil

    return tuple(psutil.Process(pid).cmdline())


class ProcessRegistry:
    """The one place a `Process` comes from.

    A pid is the operating system's and it hands the same one out again; a
    `Process` is gcmon's and never repeats. :meth:`create` is the only
    thing that mints one, so evidence naming a pid nobody created is
    dropped rather than opening a process that was never monitored
    (ADR-0025).

    The monitor writes and the control server reads from its own thread,
    so every access takes the lock. One write per process against one read
    per control message leaves nothing to contend over.
    """

    def __init__(self, cmdline_provider: CmdlineProvider | None = None) -> None:
        self._lock = threading.Lock()
        self._live: dict[int, Process] = {}
        # Per pid, every process that has left and when, oldest first.
        self._retired: dict[int, list[tuple[Process, int]]] = {}
        self._cmdline_provider = cmdline_provider

    def create(self, pid: int, ts: int) -> Process:
        """Mint the process now holding *pid*, discovered at *ts*.

        The monitor calls this, and nothing else does: a process gcmon has
        not polled is one it knows nothing about, so evidence naming that
        pid belongs to no process rather than opening one (ADR-0025).

        The command line is read here, while the process is running: read
        at the first flush it may be gone, and read once per pid it names
        the first process's program on every later one (ADR-0010).
        """
        cmdline = self._read_cmdline(pid)
        with self._lock:
            assert pid not in self._live, f"PID {pid} is held by {self._live[pid]}; retire it before creating another"
            process = Process(pid, len(self._retired.get(pid, ())) + 1, ts, cmdline)
            self._live[pid] = process
            return process

    def _read_cmdline(self, pid: int) -> tuple[str, ...] | None:
        """Read *pid*'s command line, outside the lock: the provider reads
        another process, and a control message about a third would wait
        behind it.

        A provider that fails costs its process a command line and
        nothing else. The pid has already gone, or psutil is missing.
        """
        if self._cmdline_provider is None:
            return None
        try:
            return self._cmdline_provider(pid)
        except Exception as exc:
            logger.warning("Could not collect cmdline for PID %s: %s", pid, exc)
            return None

    def retire(self, pid: int, ts: int) -> None:
        """Note that the process holding *pid* left at *ts*.

        A pid holding no process is not an error: the monitor prunes
        against a whole child listing, most of which it never minted for.
        """
        with self._lock:
            self._retire_locked(pid, ts)

    def retain(self, pids: Set[int], ts: int) -> None:
        """Retire every live process whose pid is outside *pids*."""
        with self._lock:
            for pid in self._live.keys() - pids:
                self._retire_locked(pid, ts)

    def _retire_locked(self, pid: int, ts: int) -> None:
        process = self._live.pop(pid, None)
        if process is not None:
            self._retired.setdefault(pid, []).append((process, ts))

    def live(self) -> frozenset[Process]:
        """Every process gcmon has not seen leave."""
        with self._lock:
            return frozenset(self._live.values())

    def current(self, pid: int) -> Process | None:
        """The process holding *pid* now, or ``None`` where none does."""
        with self._lock:
            return self._live.get(pid)

    def at(self, pid: int, ts: int) -> Process | None:
        """The process that held *pid* at *ts*, or ``None`` where none did.

        Evidence outlives the process it describes. A control-plane
        instant is stamped on arrival when its sender named no time, so it
        can reach gcmon after the pid was retired and still belong to the
        process that has gone; later than every retirement it reads as the
        one running now, or as the last to leave. Earlier than the first
        process's ``start_ts`` it reads as that process, since a poll
        returns collections that already happened.
        """
        with self._lock:
            for process, retired_ts in self._retired.get(pid, ()):
                if ts <= retired_ts:
                    return process
            live = self._live.get(pid)
            if live is not None:
                return live
            retired = self._retired.get(pid)
            return retired[-1][0] if retired else None

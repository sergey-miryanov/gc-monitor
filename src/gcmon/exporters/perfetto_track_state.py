"""Per-trace uuid allocation and bookkeeping, with no protobuf knowledge.

``PerfettoTrackState`` keeps descriptor emission idempotent across the
many convert calls a buffered export makes.

The ``Processes``-track span accumulator is ADR-0011 subject matter but
lives here, apart from the emission code in ``perfetto_process_lifetime``,
because splitting the class would leave two halves sharing ``_next_uuid``.
"""

from typing import NamedTuple

from ..model.process import Process
from ..model.trace_event import InterpreterTrack, LossTrack, Track

# What a row is drawn for, since a pid and an interpreter id do not tell two
# of the three kinds apart: an interpreter's row and its loss row name the
# same pair.
_INTERPRETER_ROW = "interpreter"
_LOSS_ROW = "loss"
_PROCESS_ROW = "process"

# The key a row's uuid and its emitted-flag are filed under: what the row is
# drawn for, the pid, and the interpreter it belongs to where it belongs to
# one.
type RowKey = tuple[str, int, int]

# The key a process's span is filed under, which is what `Process.__eq__`
# compares (ADR-0025).
type ProcessKey = tuple[int, int]


class ProcessSpan(NamedTuple):
    """The interval one process was observed over (ADR-0011)."""

    process: Process
    start_ts: int
    end_ts: int


def _shared_row(track: Track) -> RowKey:
    """The key *track*'s uuid is filed under.

    Every process that held a pid draws on one set of Perfetto rows, so the
    epoch is left out and the `Processes` track carries the distinction
    (ADR-0011).

    A plain tuple rather than a `Track` with its process replaced: this runs
    several times for every event the encoder writes, and minting a
    `Process` only to hash it and drop it made the write path pay for a
    struct per lookup.
    """
    if isinstance(track, InterpreterTrack):
        return (_INTERPRETER_ROW, track.process.pid, track.iid)
    if isinstance(track, LossTrack):
        return (_LOSS_ROW, track.process.pid, track.iid)
    return (_PROCESS_ROW, track.process.pid, 0)


def _process_key(process: Process) -> ProcessKey:
    """What *process* is filed under: the pid and the epoch, the pair its
    own equality is on (ADR-0025).

    Written out rather than keying on the `Process` itself, for the reason
    :func:`_shared_row` gives: its `__hash__` and `__eq__` are Python, and
    the span accumulator hits them twice per event.
    """
    return (process.pid, process.pid_epoch)


class PerfettoTrackState:
    def __init__(self) -> None:
        self._described_pids: set[int] = set()
        self._tracks: set[RowKey] = set()
        self._counter_tracks: dict[tuple[RowKey, str], int] = {}
        self._counter_group_uuids: dict[RowKey, int] = {}
        self._pid_uuids: dict[int, int] = {}
        self._track_uuids: dict[RowKey, int] = {}
        self._start_process_marker_emitted: set[int] = set()
        self._process_lifetime_track_uuid: int | None = None
        self._processes: dict[ProcessKey, Process] = {}
        self._process_lifetime_start: dict[ProcessKey, int] = {}
        self._process_lifetime_end: dict[ProcessKey, int] = {}
        self._process_lifetime_emitted: bool = False
        self._root_descriptor_emitted: bool = False
        self._next_uuid: int = 1

    def _alloc_uuid(self) -> int:
        uuid = self._next_uuid
        self._next_uuid += 1
        return uuid

    def has_process_descriptor(self, process: Process) -> bool:
        """Whether *process*'s row has been described.

        Filed under the pid, like the uuid the descriptor carries: one
        descriptor covers every process that held it.
        """
        return process.pid in self._described_pids

    def mark_process_descriptor(self, process: Process) -> None:
        self._described_pids.add(process.pid)

    def has_track(self, track: Track) -> bool:
        return _shared_row(track) in self._tracks

    def mark_track(self, track: Track) -> None:
        self._tracks.add(_shared_row(track))

    def get_process_track_uuid(self, process: Process) -> int:
        """The uuid of the row *process* draws on.

        Filed under the pid, so every process that held it shares one row,
        as :func:`_shared_row` does for the tracks underneath.
        """
        pid = process.pid
        if pid not in self._pid_uuids:
            self._pid_uuids[pid] = self._alloc_uuid()
        return self._pid_uuids[pid]

    def get_track_uuid(self, track: Track) -> int:
        key = _shared_row(track)
        if key not in self._track_uuids:
            self._track_uuids[key] = self._alloc_uuid()
        return self._track_uuids[key]

    def has_counter_track(self, track: Track, display_name: str) -> bool:
        return (_shared_row(track), display_name) in self._counter_tracks

    def get_or_create_counter_track_uuid(self, track: Track, display_name: str) -> int:
        key = (_shared_row(track), display_name)
        if key not in self._counter_tracks:
            self._counter_tracks[key] = self._alloc_uuid()
        return self._counter_tracks[key]

    def has_counter_group_track(self, track: Track) -> bool:
        return _shared_row(track) in self._counter_group_uuids

    def get_or_create_counter_group_track_uuid(self, track: Track) -> int:
        key = _shared_row(track)
        if key not in self._counter_group_uuids:
            self._counter_group_uuids[key] = self._alloc_uuid()
        return self._counter_group_uuids[key]

    def has_start_process_marker(self, process: Process) -> bool:
        return process.pid in self._start_process_marker_emitted

    def mark_start_process_marker(self, process: Process) -> None:
        self._start_process_marker_emitted.add(process.pid)

    def has_process_lifetime_track(self) -> bool:
        return self._process_lifetime_track_uuid is not None

    def get_or_create_process_lifetime_track_uuid(self) -> int:
        if self._process_lifetime_track_uuid is None:
            self._process_lifetime_track_uuid = self._alloc_uuid()
        return self._process_lifetime_track_uuid

    def has_process_lifetime(self, process: Process) -> bool:
        return _process_key(process) in self._process_lifetime_start

    def update_process_lifetime(self, process: Process, ts: int) -> None:
        """Fold *ts* into the recorded span for *process*: a plain min/max
        over every piece of evidence that gcmon saw it.

        Evidence is any event, counters included, *or* a liveness
        observation from the monitor loop. No event-kind exception: an
        RSS sample is evidence the process existed just as a GC event is.
        See ADR-0011.

        The first fold seeds both ends and files the process itself, which
        is what ``get_process_lifetimes`` hands back; a later one moves at
        most one end, since a ts below the start cannot be above the end.
        """
        key = _process_key(process)
        start_ts = self._process_lifetime_start.get(key)
        if start_ts is None:
            self._processes[key] = process
            self._process_lifetime_start[key] = ts
            self._process_lifetime_end[key] = ts
        elif ts < start_ts:
            self._process_lifetime_start[key] = ts
        elif ts > self._process_lifetime_end[key]:
            self._process_lifetime_end[key] = ts

    def get_process_lifetime_start_ts(self, process: Process) -> int | None:
        """When the pid's Perfetto row opens.

        The row is not split, so it covers every process that held the
        pid and opens at the first of them (ADR-0011).
        """
        return self._process_lifetime_start.get((process.pid, 1))

    def get_process_lifetimes(self) -> list[ProcessSpan]:
        """Every process with a recorded span.

        The three dicts carry identical key sets, so this is every process
        ever folded in -- including one known only from liveness.
        """
        return [
            ProcessSpan(self._processes[key], self._process_lifetime_start[key], end)
            for key, end in self._process_lifetime_end.items()
        ]

    def has_process_lifetime_emitted(self) -> bool:
        return self._process_lifetime_emitted

    def mark_process_lifetime_emitted(self) -> None:
        self._process_lifetime_emitted = True

    def get_process_track_ranks(self) -> dict[int, int]:
        """Return ``{pid: rank}``, assigned sequentially from ``0`` by
        ascending ``(start_ts, pid)`` over the first process to hold each
        pid. Pids with no recorded start are absent.

        Ranked on the first process for the same reason the row is stamped
        from it: one row covers them all (ADR-0011).
        """
        firsts = {pid: ts for (pid, pid_epoch), ts in self._process_lifetime_start.items() if pid_epoch == 1}
        if not firsts:
            return {}
        sorted_pids = sorted(firsts, key=lambda pid: (firsts[pid], pid))
        return {pid: rank for rank, pid in enumerate(sorted_pids)}

    def has_root_descriptor(self) -> bool:
        return self._root_descriptor_emitted

    def mark_root_descriptor_emitted(self) -> None:
        self._root_descriptor_emitted = True

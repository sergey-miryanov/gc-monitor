"""Per-trace uuid allocation and bookkeeping, with no protobuf knowledge.

``PerfettoTrackState`` keeps descriptor emission idempotent across the
many convert calls a buffered export makes.

The ``Processes``-track span accumulator is ADR-0011 subject matter but
lives here, apart from the emission code in ``perfetto_process_lifetime``,
because splitting the class would leave two halves sharing ``_next_uuid``.
"""

from typing import NamedTuple

import msgspec

from ..model.process import Process
from ..model.trace_event import Track


class ProcessSpan(NamedTuple):
    """The interval one process was observed over.

    A pid the operating system handed out twice brings one of these per
    process, so a span covers only the process it names (ADR-0011).
    """

    process: Process
    start_ts: int
    end_ts: int


def _shared_row(track: Track) -> Track:
    """The key *track*'s uuid is filed under.

    Every process that held a pid draws on one set of Perfetto rows, so the
    epoch is dropped here and two processes on one pid share a thread track,
    a counter group and its counters. The `Processes` track carries the
    distinction instead (ADR-0011).
    """
    return msgspec.structs.replace(track, process=Process(track.process.pid, 1, 0))


class PerfettoTrackState:
    def __init__(self) -> None:
        self._described_pids: set[int] = set()
        self._tracks: set[Track] = set()
        self._counter_tracks: dict[tuple[Track, str], int] = {}
        self._counter_group_uuids: dict[Track, int] = {}
        self._pid_uuids: dict[int, int] = {}
        self._track_uuids: dict[Track, int] = {}
        self._start_process_marker_emitted: set[int] = set()
        self._process_lifetime_track_uuid: int | None = None
        self._process_lifetime_start: dict[Process, int] = {}
        self._process_lifetime_end: dict[Process, int] = {}
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
        """The uuid of *process*'s own Perfetto row.

        Filed under the pid, so every process that held it shares one row,
        which is what :func:`_shared_row` does for the tracks underneath.
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
        return process in self._process_lifetime_start

    def update_process_lifetime(self, process: Process, ts: int) -> None:
        """Fold *ts* into the recorded span for *process*: a plain min/max
        over every piece of evidence that gcmon saw it.

        Evidence is any event, counters included, *or* a liveness
        observation from the monitor loop. No event-kind exception: an
        RSS sample is evidence the process existed just as a GC event is.
        See ADR-0011.

        Keyed on the process rather than the pid, so a pid handed on gets
        a span per process instead of one span across both, wide enough
        to cover a stretch in which neither was running.
        """
        start_ts = self._process_lifetime_start.get(process)
        if start_ts is None or ts < start_ts:
            self._process_lifetime_start[process] = ts
        end_ts = self._process_lifetime_end.get(process)
        if end_ts is None or ts > end_ts:
            self._process_lifetime_end[process] = ts

    def get_process_lifetime_start_ts(self, process: Process) -> int | None:
        """When the pid's Perfetto row opens.

        The row is not split, so it covers every process that held the
        pid and opens at the first of them: a run with no reuse is
        stamped exactly as it was (ADR-0011).
        """
        return self._process_lifetime_start.get(Process(process.pid, 1, 0))

    def get_process_lifetimes(self) -> list[ProcessSpan]:
        """Every process with a recorded span.

        The two dicts carry identical key sets, so this is every process
        ever folded in -- including one known only from liveness.
        """
        return [
            ProcessSpan(process, self._process_lifetime_start[process], end)
            for process, end in self._process_lifetime_end.items()
        ]

    def has_process_lifetime_emitted(self) -> bool:
        return self._process_lifetime_emitted

    def mark_process_lifetime_emitted(self) -> None:
        self._process_lifetime_emitted = True

    def get_process_track_ranks(self) -> dict[int, int]:
        """Return ``{pid: rank}``, assigned sequentially from ``0`` by
        ascending ``(start_ts, pid)`` over the first process to hold each
        pid. Pids with no recorded start are absent.

        Ranked on the first process for the same reason the row is
        stamped from it: one row covers them all, so a later process on a
        reused pid does not reorder it.
        """
        firsts = {process.pid: ts for process, ts in self._process_lifetime_start.items() if process.pid_epoch == 1}
        if not firsts:
            return {}
        sorted_pids = sorted(firsts, key=lambda pid: (firsts[pid], pid))
        return {pid: rank for rank, pid in enumerate(sorted_pids)}

    def has_root_descriptor(self) -> bool:
        return self._root_descriptor_emitted

    def mark_root_descriptor_emitted(self) -> None:
        self._root_descriptor_emitted = True

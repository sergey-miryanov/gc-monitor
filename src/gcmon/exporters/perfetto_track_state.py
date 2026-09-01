"""Per-trace uuid allocation and bookkeeping, with no protobuf knowledge.

``PerfettoTrackState`` keeps descriptor emission idempotent across the
many convert calls a buffered export makes.

It also holds the ``Processes``-track span accumulator (ADR-0011).
"""

from typing import NamedTuple

from ..model.process import Process
from ..model.trace_event import Track


class ProcessSpan(NamedTuple):
    """The interval one process was observed over (ADR-0011)."""

    process: Process
    start_ts: int
    end_ts: int


class PerfettoTrackState:
    def __init__(self) -> None:
        self._described_processes: set[Process] = set()
        self._tracks: set[Track] = set()
        self._counter_tracks: dict[tuple[Track, str], int] = {}
        self._counter_group_uuids: dict[Track, int] = {}
        self._process_uuids: dict[Process, int] = {}
        self._track_uuids: dict[Track, int] = {}
        self._process_lifetime_track_uuid: int | None = None
        self._cmdlines: dict[Process, tuple[str, ...]] = {}
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
        return process in self._described_processes

    def mark_process_descriptor(self, process: Process) -> None:
        self._described_processes.add(process)

    def has_track(self, track: Track) -> bool:
        return track in self._tracks

    def mark_track(self, track: Track) -> None:
        self._tracks.add(track)

    def get_process_track_uuid(self, process: Process) -> int:
        if process not in self._process_uuids:
            self._process_uuids[process] = self._alloc_uuid()
        return self._process_uuids[process]

    def get_track_uuid(self, track: Track) -> int:
        if track not in self._track_uuids:
            self._track_uuids[track] = self._alloc_uuid()
        return self._track_uuids[track]

    def has_counter_track(self, track: Track, display_name: str) -> bool:
        return (track, display_name) in self._counter_tracks

    def get_or_create_counter_track_uuid(self, track: Track, display_name: str) -> int:
        key = (track, display_name)
        if key not in self._counter_tracks:
            self._counter_tracks[key] = self._alloc_uuid()
        return self._counter_tracks[key]

    def has_counter_group_track(self, track: Track) -> bool:
        return track in self._counter_group_uuids

    def get_or_create_counter_group_track_uuid(self, track: Track) -> int:
        if track not in self._counter_group_uuids:
            self._counter_group_uuids[track] = self._alloc_uuid()
        return self._counter_group_uuids[track]

    def has_process_lifetime_track(self) -> bool:
        return self._process_lifetime_track_uuid is not None

    def get_or_create_process_lifetime_track_uuid(self) -> int:
        if self._process_lifetime_track_uuid is None:
            self._process_lifetime_track_uuid = self._alloc_uuid()
        return self._process_lifetime_track_uuid

    def set_cmdline(self, process: Process, cmdline: tuple[str, ...] | None) -> None:
        """Record what *process* is running, for its descriptor and its
        ``Processes``-track span to name."""
        if cmdline is not None:
            self._cmdlines[process] = cmdline

    def get_cmdline(self, process: Process) -> tuple[str, ...] | None:
        """What *process* is running, or ``None``.

        ``None`` on every offline path: a capture carries no command line
        and `combine` creates no process (ADR-0024).
        """
        return self._cmdlines.get(process)

    def has_process_lifetime(self, process: Process) -> bool:
        return process in self._process_lifetime_start

    def update_process_lifetime(self, process: Process, ts: int) -> None:
        """Fold *ts* into the recorded span for *process*: a plain min/max
        over every event and every liveness observation, with no
        event-kind exception (ADR-0011).
        """
        start_ts = self._process_lifetime_start.get(process)
        if start_ts is None or ts < start_ts:
            self._process_lifetime_start[process] = ts
        end_ts = self._process_lifetime_end.get(process)
        if end_ts is None or ts > end_ts:
            self._process_lifetime_end[process] = ts

    def get_process_lifetime_start_ts(self, process: Process) -> int | None:
        """When *process*'s Perfetto row opens."""
        return self._process_lifetime_start.get(process)

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

    def get_process_track_ranks(self) -> dict[Process, int]:
        """Return ``{process: rank}``, assigned sequentially from ``0`` by
        ascending ``(start_ts, process)``. A process with no recorded start
        is absent.
        """
        starts = self._process_lifetime_start
        ordered = sorted(starts, key=lambda process: (starts[process], process))
        return {process: rank for rank, process in enumerate(ordered)}

    def has_root_descriptor(self) -> bool:
        return self._root_descriptor_emitted

    def mark_root_descriptor_emitted(self) -> None:
        self._root_descriptor_emitted = True

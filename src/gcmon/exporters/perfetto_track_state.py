"""Per-trace uuid allocation and bookkeeping, with no protobuf knowledge.

``PerfettoTrackState`` keeps descriptor emission idempotent across the
many convert calls a buffered export makes.

The ``Processes``-track span accumulator is ADR-0011 subject matter but
lives here, apart from the emission code in ``perfetto_process_lifetime``,
because splitting the class would leave two halves sharing ``_next_uuid``.
"""

from ..model.trace_event import Track


class PerfettoTrackState:
    def __init__(self) -> None:
        self._pids: set[int] = set()
        self._tracks: set[Track] = set()
        self._cmdlines: dict[int, list[str]] = {}
        self._counter_tracks: dict[tuple[Track, str], int] = {}
        self._counter_group_uuids: dict[Track, int] = {}
        self._pid_uuids: dict[int, int] = {}
        self._track_uuids: dict[Track, int] = {}
        self._start_process_marker_emitted: set[int] = set()
        self._process_lifetime_track_uuid: int | None = None
        self._process_lifetime_start: dict[int, int] = {}
        self._process_lifetime_end: dict[int, int] = {}
        self._process_lifetime_emitted: bool = False
        self._root_descriptor_emitted: bool = False
        self._next_uuid: int = 1

    def _alloc_uuid(self) -> int:
        uuid = self._next_uuid
        self._next_uuid += 1
        return uuid

    def has_pid(self, pid: int) -> bool:
        return pid in self._pids

    def mark_pid(self, pid: int) -> None:
        self._pids.add(pid)

    def has_track(self, track: Track) -> bool:
        return track in self._tracks

    def mark_track(self, track: Track) -> None:
        self._tracks.add(track)

    def set_cmdline(self, pid: int, cmdline: list[str]) -> None:
        self._cmdlines[pid] = cmdline

    def get_cmdline(self, pid: int) -> list[str] | None:
        return self._cmdlines.get(pid)

    def get_process_track_uuid(self, pid: int) -> int:
        if pid not in self._pid_uuids:
            self._pid_uuids[pid] = self._alloc_uuid()
        return self._pid_uuids[pid]

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

    def has_start_process_marker(self, pid: int) -> bool:
        return pid in self._start_process_marker_emitted

    def mark_start_process_marker(self, pid: int) -> None:
        self._start_process_marker_emitted.add(pid)

    def has_process_lifetime_track(self) -> bool:
        return self._process_lifetime_track_uuid is not None

    def get_or_create_process_lifetime_track_uuid(self) -> int:
        if self._process_lifetime_track_uuid is None:
            self._process_lifetime_track_uuid = self._alloc_uuid()
        return self._process_lifetime_track_uuid

    def has_process_lifetime(self, pid: int) -> bool:
        return pid in self._process_lifetime_start

    def update_process_lifetime(self, pid: int, ts: int) -> None:
        """Fold *ts* into the recorded span for *pid*: a plain min/max
        over every piece of evidence that gcmon saw the process.

        Evidence is any event, counters included, *or* a liveness
        observation from the monitor loop. No event-kind exception: an
        RSS sample is evidence the process existed just as a GC event is.
        See ADR-0011.
        """
        start_ts = self._process_lifetime_start.get(pid)
        if start_ts is None or ts < start_ts:
            self._process_lifetime_start[pid] = ts
        end_ts = self._process_lifetime_end.get(pid)
        if end_ts is None or ts > end_ts:
            self._process_lifetime_end[pid] = ts

    def get_process_lifetime_start_ts(self, pid: int) -> int | None:
        return self._process_lifetime_start.get(pid)

    def get_process_lifetimes(self) -> list[tuple[int, int, int]]:
        """Return ``[(pid, start_ts, end_ts), ...]`` for every pid with a
        recorded span.

        The two dicts carry identical key sets, so this is every pid ever
        folded in -- including one known only from liveness.
        """
        return [(pid, self._process_lifetime_start[pid], end) for pid, end in self._process_lifetime_end.items()]

    def has_process_lifetime_emitted(self) -> bool:
        return self._process_lifetime_emitted

    def mark_process_lifetime_emitted(self) -> None:
        self._process_lifetime_emitted = True

    def get_process_track_ranks(self) -> dict[int, int]:
        """Return ``{pid: rank}``, assigned sequentially from ``0`` by
        ascending ``(start_ts, pid)``. Pids with no recorded start are
        absent."""
        if not self._process_lifetime_start:
            return {}
        sorted_pids = sorted(
            self._process_lifetime_start.keys(),
            key=lambda p: (self._process_lifetime_start[p], p),
        )
        return {pid: rank for rank, pid in enumerate(sorted_pids)}

    def has_root_descriptor(self) -> bool:
        return self._root_descriptor_emitted

    def mark_root_descriptor_emitted(self) -> None:
        self._root_descriptor_emitted = True

"""Per-trace uuid allocation and bookkeeping, with no protobuf knowledge.

``PerfettoTrackState`` keeps descriptor emission idempotent across the
many convert calls a buffered export makes.

The ``Processes``-track span accumulator is ADR-0011 subject matter but
lives here, apart from the emission code in ``perfetto_process_lifetime``,
because splitting the class would leave two halves sharing ``_next_uuid``.
"""


class PerfettoTrackState:
    def __init__(self) -> None:
        self._pids: set[int] = set()
        self._tids: set[tuple[int, int]] = set()
        self._cmdlines: dict[int, list[str]] = {}
        self._counter_tracks: dict[tuple[int, int, str, str], int] = {}
        self._counter_group_uuids: dict[tuple[int, int], int] = {}
        self._pid_uuids: dict[int, int] = {}
        self._tid_uuids: dict[tuple[int, int], int] = {}
        self._start_process_marker_emitted: set[int] = set()
        self._process_lifetime_track_uuid: int | None = None
        self._process_lifetime_start: dict[int, int] = {}
        self._process_lifetime_end: dict[int, int] = {}
        self._process_lifetime_drained: bool = False
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

    def has_tid(self, pid: int, iid: int) -> bool:
        return (pid, iid) in self._tids

    def mark_tid(self, pid: int, iid: int) -> None:
        self._tids.add((pid, iid))

    def set_cmdline(self, pid: int, cmdline: list[str]) -> None:
        self._cmdlines[pid] = cmdline

    def get_cmdline(self, pid: int) -> list[str] | None:
        return self._cmdlines.get(pid)

    def get_process_track_uuid(self, pid: int) -> int:
        if pid not in self._pid_uuids:
            self._pid_uuids[pid] = self._alloc_uuid()
        return self._pid_uuids[pid]

    def get_thread_track_uuid(self, pid: int, iid: int) -> int:
        key = (pid, iid)
        if key not in self._tid_uuids:
            self._tid_uuids[key] = self._alloc_uuid()
        return self._tid_uuids[key]

    def has_counter_track(self, pid: int, iid: int, name: str, metric: str) -> bool:
        return (pid, iid, name, metric) in self._counter_tracks

    def get_or_create_counter_track_uuid(self, pid: int, iid: int, name: str, metric: str) -> int:
        key = (pid, iid, name, metric)
        if key not in self._counter_tracks:
            self._counter_tracks[key] = self._alloc_uuid()
        return self._counter_tracks[key]

    def has_counter_group_track(self, pid: int, iid: int) -> bool:
        return (pid, iid) in self._counter_group_uuids

    def get_or_create_counter_group_track_uuid(self, pid: int, iid: int) -> int:
        key = (pid, iid)
        if key not in self._counter_group_uuids:
            self._counter_group_uuids[key] = self._alloc_uuid()
        return self._counter_group_uuids[key]

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

    def update_process_lifetime(self, pid: int, ts: int, *, extends_end: bool) -> None:
        """Fold *ts* into the recorded span for *pid*.

        The start is a minimum over every non-meta event; the end is a
        maximum over non-counter events only, so *extends_end* must be
        ``False`` for a ``CounterEvent``. The two are held separately so
        a counter can never seed the end, not even as the first event
        folded for a pid; a counter-only pid therefore keeps its rank but
        gets no slice. ADR-0011 has the rationale, and why the asymmetry
        is provisional.
        """
        start_ts = self._process_lifetime_start.get(pid)
        if start_ts is None or ts < start_ts:
            self._process_lifetime_start[pid] = ts
        if not extends_end:
            return
        end_ts = self._process_lifetime_end.get(pid)
        if end_ts is None or ts > end_ts:
            self._process_lifetime_end[pid] = ts

    def get_process_lifetime_start_ts(self, pid: int) -> int | None:
        return self._process_lifetime_start.get(pid)

    def pop_process_lifetimes(self) -> list[tuple[int, int, int]]:
        """Return ``[(pid, start_ts, end_ts), ...]`` for every pid with
        both a start and an end, sorted by ``(start_ts, -end_ts, pid)`` --
        the order ``_clip_spans_to_laminar`` requires. A pid seen only
        through counters has no end and is absent.

        Drains: a second call returns an empty list, which is what makes
        ``finalize_perfetto_packets`` safe to call twice. The spans
        themselves are kept, so the query methods above keep working.
        """
        if self._process_lifetime_drained:
            return []
        self._process_lifetime_drained = True
        return sorted(
            ((pid, self._process_lifetime_start[pid], end) for pid, end in self._process_lifetime_end.items()),
            key=lambda item: (item[1], -item[2], item[0]),
        )

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

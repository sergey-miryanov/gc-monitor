"""Per-trace uuid allocation and bookkeeping, with no protobuf knowledge.

``PerfettoTrackState`` keeps descriptor emission idempotent across the
many convert calls a buffered export makes.

The ``Processes``-track span accumulator is ADR-0011 subject matter but
lives here, apart from the emission code in ``perfetto_process_lifetime``,
because splitting the class would leave two halves sharing ``_next_uuid``.
"""

from collections.abc import Set

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
        self._process_lifetime_start: dict[tuple[int, int], int] = {}
        self._process_lifetime_end: dict[tuple[int, int], int] = {}
        # Which process holds each pid, counting from 1; the pids whose span
        # is still open; and, per pid, the end of the last span that closed.
        self._pid_epochs: dict[int, int] = {}
        self._open_pids: set[int] = set()
        self._process_lifetime_closed_end: dict[int, int] = {}
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
        return (pid, 1) in self._process_lifetime_start

    def update_process_lifetime(self, pid: int, ts: int) -> None:
        """Fold *ts* into the recorded span of whichever process holds
        *pid* now: a plain min/max over every piece of evidence that
        gcmon saw it.

        Evidence is any event, counters included, *or* a liveness
        observation from the monitor loop. No event-kind exception: an
        RSS sample is evidence the process existed just as a GC event is.
        See ADR-0011.

        Evidence for a pid whose span :meth:`observe_process_liveness`
        closed opens the next one, so a pid the operating system handed
        out twice keeps a span per process rather than one span across
        both.
        """
        key = (pid, self._epoch_for(pid, ts))
        start_ts = self._process_lifetime_start.get(key)
        if start_ts is None or ts < start_ts:
            self._process_lifetime_start[key] = ts
        end_ts = self._process_lifetime_end.get(key)
        if end_ts is None or ts > end_ts:
            self._process_lifetime_end[key] = ts

    def _epoch_for(self, pid: int, ts: int) -> int:
        """Which process holding *pid* evidence at *ts* belongs to.

        Evidence later than every span recorded so far extends the one
        still open, or opens the next one where the last was closed.
        Evidence no later than a span that has closed belongs to *that*
        span instead: a pid pruned from the process tree loses its read
        cursors, so whatever claims it next re-exports records its
        predecessor already produced, and the arrival order of a batch
        says nothing about which process a record came from.

        Two spans on one pid therefore never overlap. Both carry the same
        pid in their name, and a named ``TYPE_SLICE_END`` can only pick
        the right ``TYPE_SLICE_BEGIN`` while no two of that name are open
        at once (ADR-0011).
        """
        closed_end = self._process_lifetime_closed_end.get(pid)
        if closed_end is not None and ts <= closed_end:
            return next(
                pid_epoch
                for pid_epoch in range(1, self._pid_epochs[pid] + 1)
                if ts <= self._process_lifetime_end[(pid, pid_epoch)]
            )
        if pid in self._open_pids:
            return self._pid_epochs[pid]
        pid_epoch = self._pid_epochs.get(pid, 0) + 1
        self._pid_epochs[pid] = pid_epoch
        self._open_pids.add(pid)
        return pid_epoch

    def observe_process_liveness(self, pids: Set[int], ts: int) -> None:
        """Fold one tick's liveness observations in, and close the span
        of every pid the tick did not report.

        A pid absent from one report and present in a later one is a new
        process, the rule `StreamingStats` applies to the exits it sees.
        Evidence still to arrive for a closed pid opens a span of its
        own, so the caller has to hand its buffered events over before a
        report that drops one. See ADR-0011.
        """
        for pid in self._open_pids - set(pids):
            self._process_lifetime_closed_end[pid] = self._process_lifetime_end[(pid, self._pid_epochs[pid])]
        self._open_pids.intersection_update(pids)
        for pid in pids:
            self.update_process_lifetime(pid, ts)

    def get_process_lifetime_start_ts(self, pid: int) -> int | None:
        """When *pid*'s process track opens.

        The track is not split, so it covers every process that held the
        pid and the first of them is when it opens (ADR-0011).
        """
        return self._process_lifetime_start.get((pid, 1))

    def get_process_lifetimes(self) -> list[tuple[int, int, int, int]]:
        """Return ``[(pid, pid_epoch, start_ts, end_ts), ...]`` for every
        process with a recorded span.

        The two dicts carry identical key sets, so this is every process
        ever folded in -- including one known only from liveness.
        """
        return [
            (pid, pid_epoch, self._process_lifetime_start[(pid, pid_epoch)], end)
            for (pid, pid_epoch), end in self._process_lifetime_end.items()
        ]

    def has_process_lifetime_emitted(self) -> bool:
        return self._process_lifetime_emitted

    def mark_process_lifetime_emitted(self) -> None:
        self._process_lifetime_emitted = True

    def get_process_track_ranks(self) -> dict[int, int]:
        """Return ``{pid: rank}``, assigned sequentially from ``0`` by
        ascending ``(start_ts, pid)`` over the first process to hold each
        pid. Pids with no recorded start are absent.

        Ranked on the same timestamp the track opens at, so a pid handed
        out twice leaves process order as a run without reuse writes it.
        """
        starts = {pid: ts for (pid, pid_epoch), ts in self._process_lifetime_start.items() if pid_epoch == 1}
        if not starts:
            return {}
        return {pid: rank for rank, pid in enumerate(sorted(starts, key=lambda p: (starts[p], p)))}

    def has_root_descriptor(self) -> bool:
        return self._root_descriptor_emitted

    def mark_root_descriptor_emitted(self) -> None:
        self._root_descriptor_emitted = True

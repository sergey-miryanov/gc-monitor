"""Tests for ``PerfettoTrackState`` uuid allocation and bookkeeping."""

from gcmon.exporters.perfetto_track_state import PerfettoTrackState
from gcmon.model.trace_event import ThreadTrack


class TestPerfettoTrackState:
    def test_init_empty(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_pid(123)
        assert not state.has_track(ThreadTrack(123, 0))
        assert not state.has_counter_track(ThreadTrack(123, 0), "G0 collected")

    def test_pid_tracking(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_pid(100)
        state.mark_pid(100)
        assert state.has_pid(100)
        assert not state.has_pid(200)

    def test_tid_tracking(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_track(ThreadTrack(100, 0))
        state.mark_track(ThreadTrack(100, 0))
        assert state.has_track(ThreadTrack(100, 0))
        assert not state.has_track(ThreadTrack(100, 1))
        assert not state.has_track(ThreadTrack(200, 0))

    def test_process_track_uuid(self) -> None:
        state = PerfettoTrackState()
        uuid = state.get_process_track_uuid(12345)
        assert uuid == 1

    def test_thread_track_uuid(self) -> None:
        state = PerfettoTrackState()
        uuid = state.get_track_uuid(ThreadTrack(12345, 0))
        assert uuid == 1

    def test_thread_track_uuid_different_iid(self) -> None:
        state = PerfettoTrackState()
        uuid0 = state.get_track_uuid(ThreadTrack(12345, 0))
        uuid1 = state.get_track_uuid(ThreadTrack(12345, 1))
        assert uuid0 != uuid1

    def test_counter_track_uuid_sequential(self) -> None:
        state = PerfettoTrackState()
        uuid0 = state.get_or_create_counter_track_uuid(ThreadTrack(100, 0), "G0 collected")
        uuid1 = state.get_or_create_counter_track_uuid(ThreadTrack(100, 0), "heap_size")
        assert uuid0 == 1
        assert uuid1 == 2

    def test_counter_track_uuid_idempotent(self) -> None:
        state = PerfettoTrackState()
        uuid1 = state.get_or_create_counter_track_uuid(ThreadTrack(100, 0), "G0 collected")
        uuid2 = state.get_or_create_counter_track_uuid(ThreadTrack(100, 0), "G0 collected")
        assert uuid1 == uuid2

    def test_has_counter_track(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_counter_track(ThreadTrack(100, 0), "G0 collected")
        state.get_or_create_counter_track_uuid(ThreadTrack(100, 0), "G0 collected")
        assert state.has_counter_track(ThreadTrack(100, 0), "G0 collected")
        assert not state.has_counter_track(ThreadTrack(100, 0), "G1 collected")


class TestProcessLifetimeState:
    """State accessors for the shared ``Processes`` track."""

    def test_track_uuid_lazy_and_idempotent(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_process_lifetime_track()
        uuid1 = state.get_or_create_process_lifetime_track_uuid()
        assert state.has_process_lifetime_track()
        uuid2 = state.get_or_create_process_lifetime_track_uuid()
        assert uuid1 == uuid2

    def test_track_uuid_distinct_from_process_uuid(self) -> None:
        state = PerfettoTrackState()
        proc_uuid = state.get_process_track_uuid(100)
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        assert lifetime_uuid != proc_uuid

    def test_first_update_seeds_both_ends(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_process_lifetime(100)
        state.update_process_lifetime(100, 1_000)
        assert state.has_process_lifetime(100)
        assert state.get_process_lifetimes() == [(100, 1_000, 1_000)]
        assert not state.has_process_lifetime(200)

    def test_span_widens_in_both_directions(self) -> None:
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 2_000)
        state.update_process_lifetime(100, 5_000)
        state.update_process_lifetime(100, 1_000)
        state.update_process_lifetime(100, 3_000)  # inside; no effect
        assert state.get_process_lifetimes() == [(100, 1_000, 5_000)]

    def test_a_counter_widens_both_ends(self) -> None:
        """The reverse of the old rule, where a counter moved the start
        and never the end. An RSS sample at 9us is evidence the process
        was alive at 9us exactly as a GC event would be, and the caller
        no longer says which kind it is holding."""
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 2_000)
        state.update_process_lifetime(100, 4_000)
        # A counter before the span's start pulls the start back...
        state.update_process_lifetime(100, 1_000)
        # ...and one after its end pushes the end out.
        state.update_process_lifetime(100, 9_000)
        assert state.get_process_lifetimes() == [(100, 1_000, 9_000)]

    def test_counter_only_pid_gets_a_span(self) -> None:
        """A pid seen only through counters used to get a start, and
        therefore a rank, but no span and no slice. It now gets both."""
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 1_000)
        state.update_process_lifetime(100, 7_000)
        assert state.has_process_lifetime(100)
        assert state.get_process_lifetime_start_ts(100) == 1_000
        assert state.get_process_lifetimes() == [(100, 1_000, 7_000)]

    def test_a_leading_counter_seeds_the_end(self) -> None:
        """A counter sets the end like anything else, including when it
        is the first event folded for a pid.

        Events reach the encoder in buffer order, not timestamp order: a
        poll returns GC events that already happened, while an RSS sample
        is stamped when taken, so a counter can arrive first and carry a
        later ts than every GC event in the batch.
        """
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 1_000)
        state.update_process_lifetime(100, 500)
        state.update_process_lifetime(100, 600)
        assert state.get_process_lifetimes() == [(100, 500, 1_000)]

    def test_both_ends_always_carry_the_same_pids(self) -> None:
        """One call gives a pid both a start and an end, so no pid can
        land in one dict and not the other -- which is what lets
        ``get_process_lifetimes`` index the start dict while iterating
        the end one."""
        state = PerfettoTrackState()
        for pid, ts in ((100, 1_000), (200, 2_000), (100, 3_000)):
            state.update_process_lifetime(pid, ts)
        assert state._process_lifetime_start.keys() == state._process_lifetime_end.keys()
        assert sorted(state.get_process_lifetimes()) == [(100, 1_000, 3_000), (200, 2_000, 2_000)]

    def test_get_returns_every_span_regardless_of_order(self) -> None:
        """Order is not part of the contract -- ``_clip_spans_to_laminar``
        sorts what it needs -- so this pins the contents only."""
        state = PerfettoTrackState()
        # Deliberately inserted out of order, with a tie on start ts
        # between pids 300 and 100.
        for pid, start, end in (
            (200, 2_000, 3_000),
            (300, 1_000, 4_000),
            (100, 1_000, 9_000),
        ):
            state.update_process_lifetime(pid, start)
            state.update_process_lifetime(pid, end)
        assert sorted(state.get_process_lifetimes()) == [
            (100, 1_000, 9_000),
            (200, 2_000, 3_000),
            (300, 1_000, 4_000),
        ]

    def test_get_does_not_drain(self) -> None:
        """Reading spans has no side effect: the once-per-trace contract
        is ``finalize_perfetto_packets``' flag, not a drain here."""
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 1_000)
        assert state.get_process_lifetimes() == [(100, 1_000, 1_000)]
        assert state.get_process_lifetimes() == [(100, 1_000, 1_000)]
        assert state.has_process_lifetime(100)
        assert state.get_process_lifetime_start_ts(100) == 1_000

    def test_process_lifetime_emitted_flag(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_process_lifetime_emitted()
        state.mark_process_lifetime_emitted()
        assert state.has_process_lifetime_emitted()

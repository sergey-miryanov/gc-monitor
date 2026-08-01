"""Tests for ``PerfettoTrackState`` uuid allocation and bookkeeping."""

from gcmon.exporters.perfetto_track_state import PerfettoTrackState


class TestPerfettoTrackState:
    def test_init_empty(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_pid(123)
        assert not state.has_tid(123, 0)
        assert not state.has_counter_track(123, 0, "G0", "collected")

    def test_pid_tracking(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_pid(100)
        state.mark_pid(100)
        assert state.has_pid(100)
        assert not state.has_pid(200)

    def test_tid_tracking(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_tid(100, 0)
        state.mark_tid(100, 0)
        assert state.has_tid(100, 0)
        assert not state.has_tid(100, 1)
        assert not state.has_tid(200, 0)

    def test_process_track_uuid(self) -> None:
        state = PerfettoTrackState()
        uuid = state.get_process_track_uuid(12345)
        assert uuid == 1

    def test_thread_track_uuid(self) -> None:
        state = PerfettoTrackState()
        uuid = state.get_thread_track_uuid(12345, 0)
        assert uuid == 1

    def test_thread_track_uuid_different_iid(self) -> None:
        state = PerfettoTrackState()
        uuid0 = state.get_thread_track_uuid(12345, 0)
        uuid1 = state.get_thread_track_uuid(12345, 1)
        assert uuid0 != uuid1

    def test_counter_track_uuid_sequential(self) -> None:
        state = PerfettoTrackState()
        uuid0 = state.get_or_create_counter_track_uuid(100, 0, "G0", "collected")
        uuid1 = state.get_or_create_counter_track_uuid(100, 0, "G0", "heap_size")
        assert uuid0 == 1
        assert uuid1 == 2

    def test_counter_track_uuid_idempotent(self) -> None:
        state = PerfettoTrackState()
        uuid1 = state.get_or_create_counter_track_uuid(100, 0, "G0", "collected")
        uuid2 = state.get_or_create_counter_track_uuid(100, 0, "G0", "collected")
        assert uuid1 == uuid2

    def test_has_counter_track(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_counter_track(100, 0, "G0", "collected")
        state.get_or_create_counter_track_uuid(100, 0, "G0", "collected")
        assert state.has_counter_track(100, 0, "G0", "collected")
        assert not state.has_counter_track(100, 0, "G1", "collected")


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
        state.update_process_lifetime(100, 1_000, extends_end=True)
        assert state.has_process_lifetime(100)
        assert state.pop_process_lifetimes() == [(100, 1_000, 1_000)]
        assert not state.has_process_lifetime(200)

    def test_span_widens_in_both_directions(self) -> None:
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 2_000, extends_end=True)
        state.update_process_lifetime(100, 5_000, extends_end=True)
        state.update_process_lifetime(100, 1_000, extends_end=True)
        state.update_process_lifetime(100, 3_000, extends_end=True)  # inside; no effect
        assert state.pop_process_lifetimes() == [(100, 1_000, 5_000)]

    def test_counter_moves_start_but_never_end(self) -> None:
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 2_000, extends_end=True)
        state.update_process_lifetime(100, 4_000, extends_end=True)
        # A counter before the span's start still pulls the start back...
        state.update_process_lifetime(100, 1_000, extends_end=False)
        # ...but one after its end leaves the end alone.
        state.update_process_lifetime(100, 9_000, extends_end=False)
        assert state.pop_process_lifetimes() == [(100, 1_000, 4_000)]

    def test_counter_only_pid_gets_no_span(self) -> None:
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 1_000, extends_end=False)
        state.update_process_lifetime(100, 7_000, extends_end=False)
        # A start, and therefore a rank, but nothing to draw a span over.
        assert state.has_process_lifetime(100)
        assert state.get_process_lifetime_start_ts(100) == 1_000
        assert state.pop_process_lifetimes() == []

    def test_leading_counter_does_not_seed_the_end(self) -> None:
        """A counter cannot set the end even when it is the first event
        folded for a pid.

        Events reach the encoder in buffer order, not timestamp order:
        a poll returns GC events that already happened, while an RSS
        sample is stamped when it is taken. So a counter can arrive
        first for a pid and carry a later ts than every GC event in the
        same batch.
        """
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 1_000, extends_end=False)
        state.update_process_lifetime(100, 500, extends_end=True)
        state.update_process_lifetime(100, 600, extends_end=True)
        assert state.pop_process_lifetimes() == [(100, 500, 600)]

    def test_pop_sorted_by_start_then_longest_then_pid(self) -> None:
        state = PerfettoTrackState()
        # Deliberately inserted out of order, with a tie on start ts
        # between pids 300 and 100 so both tiebreakers are exercised.
        for pid, start, end in (
            (200, 2_000, 3_000),
            (300, 1_000, 4_000),
            (100, 1_000, 9_000),
        ):
            state.update_process_lifetime(pid, start, extends_end=True)
            state.update_process_lifetime(pid, end, extends_end=True)
        assert state.pop_process_lifetimes() == [
            (100, 1_000, 9_000),  # same start as 300, but longer -> first
            (300, 1_000, 4_000),
            (200, 2_000, 3_000),
        ]

    def test_pop_drains_but_keeps_spans_queryable(self) -> None:
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 1_000, extends_end=True)
        state.pop_process_lifetimes()
        assert state.has_process_lifetime(100)
        assert state.get_process_lifetime_start_ts(100) == 1_000
        assert state.pop_process_lifetimes() == []

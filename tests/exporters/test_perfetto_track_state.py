"""Tests for ``PerfettoTrackState`` uuid allocation and bookkeeping."""

from gcmon.exporters.perfetto_track_state import PerfettoTrackState
from tests.exporters.perfetto_helpers import span
from tests.helpers import interpreter_track, loss_track, proc


class TestPerfettoTrackState:
    def test_init_empty(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_process_descriptor(proc(123))
        assert not state.has_track(interpreter_track(123, 0))
        assert not state.has_counter_track(interpreter_track(123, 0), "G0 collected")

    def test_pid_tracking(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_process_descriptor(proc(100))
        state.mark_process_descriptor(proc(100))
        assert state.has_process_descriptor(proc(100))
        assert not state.has_process_descriptor(proc(200))

    def test_tid_tracking(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_track(interpreter_track(100, 0))
        state.mark_track(interpreter_track(100, 0))
        assert state.has_track(interpreter_track(100, 0))
        assert not state.has_track(interpreter_track(100, 1))
        assert not state.has_track(interpreter_track(200, 0))

    def test_process_track_uuid(self) -> None:
        state = PerfettoTrackState()
        uuid = state.get_process_track_uuid(proc(12345))
        assert uuid == 1

    def test_thread_track_uuid(self) -> None:
        state = PerfettoTrackState()
        uuid = state.get_track_uuid(interpreter_track(12345, 0))
        assert uuid == 1

    def test_thread_track_uuid_different_iid(self) -> None:
        state = PerfettoTrackState()
        uuid0 = state.get_track_uuid(interpreter_track(12345, 0))
        uuid1 = state.get_track_uuid(interpreter_track(12345, 1))
        assert uuid0 != uuid1

    def test_counter_track_uuid_sequential(self) -> None:
        state = PerfettoTrackState()
        uuid0 = state.get_or_create_counter_track_uuid(interpreter_track(100, 0), "G0 collected")
        uuid1 = state.get_or_create_counter_track_uuid(interpreter_track(100, 0), "heap_size")
        assert uuid0 == 1
        assert uuid1 == 2

    def test_counter_track_uuid_idempotent(self) -> None:
        state = PerfettoTrackState()
        uuid1 = state.get_or_create_counter_track_uuid(interpreter_track(100, 0), "G0 collected")
        uuid2 = state.get_or_create_counter_track_uuid(interpreter_track(100, 0), "G0 collected")
        assert uuid1 == uuid2

    def test_has_counter_track(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_counter_track(interpreter_track(100, 0), "G0 collected")
        state.get_or_create_counter_track_uuid(interpreter_track(100, 0), "G0 collected")
        assert state.has_counter_track(interpreter_track(100, 0), "G0 collected")
        assert not state.has_counter_track(interpreter_track(100, 0), "G1 collected")


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
        proc_uuid = state.get_process_track_uuid(proc(100))
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        assert lifetime_uuid != proc_uuid

    def test_first_update_seeds_both_ends(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_process_lifetime(proc(100))
        state.update_process_lifetime(proc(100), 1_000)
        assert state.has_process_lifetime(proc(100))
        assert state.get_process_lifetimes() == [span(100, 1_000, 1_000)]
        assert not state.has_process_lifetime(proc(200))

    def test_span_widens_in_both_directions(self) -> None:
        state = PerfettoTrackState()
        state.update_process_lifetime(proc(100), 2_000)
        state.update_process_lifetime(proc(100), 5_000)
        state.update_process_lifetime(proc(100), 1_000)
        state.update_process_lifetime(proc(100), 3_000)  # inside; no effect
        assert state.get_process_lifetimes() == [span(100, 1_000, 5_000)]

    def test_a_counter_widens_both_ends(self) -> None:
        """The reverse of the old rule, where a counter moved the start
        and never the end. An RSS sample at 9us is evidence the process
        was alive at 9us exactly as a GC event would be, and the caller
        no longer says which kind it is holding."""
        state = PerfettoTrackState()
        state.update_process_lifetime(proc(100), 2_000)
        state.update_process_lifetime(proc(100), 4_000)
        # A counter before the span's start pulls the start back...
        state.update_process_lifetime(proc(100), 1_000)
        # ...and one after its end pushes the end out.
        state.update_process_lifetime(proc(100), 9_000)
        assert state.get_process_lifetimes() == [span(100, 1_000, 9_000)]

    def test_counter_only_pid_gets_a_span(self) -> None:
        """A pid seen only through counters used to get a start, and
        therefore a rank, but no span and no slice. It now gets both."""
        state = PerfettoTrackState()
        state.update_process_lifetime(proc(100), 1_000)
        state.update_process_lifetime(proc(100), 7_000)
        assert state.has_process_lifetime(proc(100))
        assert state.get_process_lifetime_start_ts(proc(100)) == 1_000
        assert state.get_process_lifetimes() == [span(100, 1_000, 7_000)]

    def test_a_leading_counter_seeds_the_end(self) -> None:
        """A counter sets the end like anything else, including when it
        is the first event folded for a pid.

        Events reach the encoder in buffer order, not timestamp order: a
        poll returns GC events that already happened, while an RSS sample
        is stamped when taken, so a counter can arrive first and carry a
        later ts than every GC event in the batch.
        """
        state = PerfettoTrackState()
        state.update_process_lifetime(proc(100), 1_000)
        state.update_process_lifetime(proc(100), 500)
        state.update_process_lifetime(proc(100), 600)
        assert state.get_process_lifetimes() == [span(100, 500, 1_000)]

    def test_both_ends_always_carry_the_same_pids(self) -> None:
        """One call gives a pid both a start and an end, so no pid can
        land in one dict and not the other -- which is what lets
        ``get_process_lifetimes`` index the start dict while iterating
        the end one."""
        state = PerfettoTrackState()
        for pid, ts in ((100, 1_000), (200, 2_000), (100, 3_000)):
            state.update_process_lifetime(proc(pid), ts)
        assert state._process_lifetime_start.keys() == state._process_lifetime_end.keys()
        assert sorted(state.get_process_lifetimes()) == [span(100, 1_000, 3_000), span(200, 2_000, 2_000)]

    def test_get_returns_every_span_regardless_of_order(self) -> None:
        """Order is not part of the contract -- ``_clip_spans_to_laminar``
        sorts what it needs -- so this pins the contents only."""
        state = PerfettoTrackState()
        # Inserted out of order, with a tie on start ts
        # between pids 300 and 100.
        for one in (
            span(200, 2_000, 3_000),
            span(300, 1_000, 4_000),
            span(100, 1_000, 9_000),
        ):
            state.update_process_lifetime(one.process, one.start_ts)
            state.update_process_lifetime(one.process, one.end_ts)
        assert sorted(state.get_process_lifetimes()) == [
            span(100, 1_000, 9_000),
            span(200, 2_000, 3_000),
            span(300, 1_000, 4_000),
        ]

    def test_get_does_not_drain(self) -> None:
        """Reading spans has no side effect: the once-per-trace contract
        is ``finalize_perfetto_packets``' flag, not a drain here."""
        state = PerfettoTrackState()
        state.update_process_lifetime(proc(100), 1_000)
        assert state.get_process_lifetimes() == [span(100, 1_000, 1_000)]
        assert state.get_process_lifetimes() == [span(100, 1_000, 1_000)]
        assert state.has_process_lifetime(proc(100))
        assert state.get_process_lifetime_start_ts(proc(100)) == 1_000

    def test_process_lifetime_emitted_flag(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_process_lifetime_emitted()
        state.mark_process_lifetime_emitted()
        assert state.has_process_lifetime_emitted()


class TestTwoProcessesOnOnePidGetTheirOwnRows:
    """A `Track` names the process it was drawn for, and every row the
    exporter allocates is filed under that process: a pid handed on draws two
    process tracks, two thread tracks, two loss tracks, two counter groups and
    two of each counter (ADR-0011)."""

    FIRST = interpreter_track(100, 0, 1)
    SECOND = interpreter_track(100, 0, 2)

    def test_the_process_track_gets_a_uuid_per_process(self) -> None:
        state = PerfettoTrackState()

        assert state.get_process_track_uuid(proc(100, 2)) != state.get_process_track_uuid(proc(100, 1))

    def test_the_process_descriptor_goes_out_per_process(self) -> None:
        state = PerfettoTrackState()
        state.mark_process_descriptor(proc(100, 1))

        assert not state.has_process_descriptor(proc(100, 2))

    def test_the_start_process_marker_goes_out_per_process(self) -> None:
        state = PerfettoTrackState()
        state.mark_start_process_marker(proc(100, 1))

        assert not state.has_start_process_marker(proc(100, 2))

    def test_the_thread_track_gets_a_uuid_per_process(self) -> None:
        state = PerfettoTrackState()

        assert state.get_track_uuid(self.SECOND) != state.get_track_uuid(self.FIRST)

    def test_the_thread_descriptor_goes_out_per_process(self) -> None:
        state = PerfettoTrackState()
        state.mark_track(self.FIRST)

        assert not state.has_track(self.SECOND)

    def test_the_loss_track_gets_a_uuid_per_process(self) -> None:
        state = PerfettoTrackState()

        assert state.get_track_uuid(loss_track(100, 0, 2)) != state.get_track_uuid(loss_track(100, 0, 1))

    def test_the_counter_group_gets_a_uuid_per_process(self) -> None:
        state = PerfettoTrackState()
        first = state.get_or_create_counter_group_track_uuid(self.FIRST)

        assert not state.has_counter_group_track(self.SECOND)
        assert state.get_or_create_counter_group_track_uuid(self.SECOND) != first

    def test_a_counter_gets_a_uuid_per_process(self) -> None:
        state = PerfettoTrackState()
        first = state.get_or_create_counter_track_uuid(self.FIRST, "G0 collected")

        assert not state.has_counter_track(self.SECOND, "G0 collected")
        assert state.get_or_create_counter_track_uuid(self.SECOND, "G0 collected") != first

    def test_two_interpreters_are_still_two_rows(self) -> None:
        """The control: dropping the epoch must not fold anything else."""
        state = PerfettoTrackState()

        assert state.get_track_uuid(interpreter_track(100, 1, 1)) != state.get_track_uuid(self.FIRST)

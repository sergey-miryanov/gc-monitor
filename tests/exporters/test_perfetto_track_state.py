"""Tests for ``PerfettoTrackState`` uuid allocation and bookkeeping."""

from gcmon.exporters.perfetto_track_state import PerfettoTrackState
from gcmon.model.trace_event import InterpreterTrack


class TestPerfettoTrackState:
    def test_init_empty(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_pid(123, 1)
        assert not state.has_track(InterpreterTrack(123, 0), 1)
        assert not state.has_counter_track(InterpreterTrack(123, 0), 1, "G0 collected")

    def test_pid_tracking(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_pid(100, 1)
        state.mark_pid(100, 1)
        assert state.has_pid(100, 1)
        assert not state.has_pid(200, 1)

    def test_tid_tracking(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_track(InterpreterTrack(100, 0), 1)
        state.mark_track(InterpreterTrack(100, 0), 1)
        assert state.has_track(InterpreterTrack(100, 0), 1)
        assert not state.has_track(InterpreterTrack(100, 1), 1)
        assert not state.has_track(InterpreterTrack(200, 0), 1)

    def test_process_track_uuid(self) -> None:
        state = PerfettoTrackState()
        uuid = state.get_process_track_uuid(12345, 1)
        assert uuid == 1

    def test_thread_track_uuid(self) -> None:
        state = PerfettoTrackState()
        uuid = state.get_track_uuid(InterpreterTrack(12345, 0), 1)
        assert uuid == 1

    def test_thread_track_uuid_different_iid(self) -> None:
        state = PerfettoTrackState()
        uuid0 = state.get_track_uuid(InterpreterTrack(12345, 0), 1)
        uuid1 = state.get_track_uuid(InterpreterTrack(12345, 1), 1)
        assert uuid0 != uuid1

    def test_counter_track_uuid_sequential(self) -> None:
        state = PerfettoTrackState()
        uuid0 = state.get_or_create_counter_track_uuid(InterpreterTrack(100, 0), 1, "G0 collected")
        uuid1 = state.get_or_create_counter_track_uuid(InterpreterTrack(100, 0), 1, "heap_size")
        assert uuid0 == 1
        assert uuid1 == 2

    def test_counter_track_uuid_idempotent(self) -> None:
        state = PerfettoTrackState()
        uuid1 = state.get_or_create_counter_track_uuid(InterpreterTrack(100, 0), 1, "G0 collected")
        uuid2 = state.get_or_create_counter_track_uuid(InterpreterTrack(100, 0), 1, "G0 collected")
        assert uuid1 == uuid2

    def test_has_counter_track(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_counter_track(InterpreterTrack(100, 0), 1, "G0 collected")
        state.get_or_create_counter_track_uuid(InterpreterTrack(100, 0), 1, "G0 collected")
        assert state.has_counter_track(InterpreterTrack(100, 0), 1, "G0 collected")
        assert not state.has_counter_track(InterpreterTrack(100, 0), 1, "G1 collected")


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
        proc_uuid = state.get_process_track_uuid(100, 1)
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        assert lifetime_uuid != proc_uuid

    def test_first_update_seeds_both_ends(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_process_lifetime(100, 1)
        state.update_process_lifetime(100, 1_000)
        assert state.has_process_lifetime(100, 1)
        assert state.get_process_lifetimes() == [(100, 1, 1_000, 1_000)]
        assert not state.has_process_lifetime(200, 1)

    def test_span_widens_in_both_directions(self) -> None:
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 2_000)
        state.update_process_lifetime(100, 5_000)
        state.update_process_lifetime(100, 1_000)
        state.update_process_lifetime(100, 3_000)  # inside; no effect
        assert state.get_process_lifetimes() == [(100, 1, 1_000, 5_000)]

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
        assert state.get_process_lifetimes() == [(100, 1, 1_000, 9_000)]

    def test_counter_only_pid_gets_a_span(self) -> None:
        """A pid seen only through counters used to get a start, and
        therefore a rank, but no span and no slice. It now gets both."""
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 1_000)
        state.update_process_lifetime(100, 7_000)
        assert state.has_process_lifetime(100, 1)
        assert state.get_process_lifetime_start_ts(100, 1) == 1_000
        assert state.get_process_lifetimes() == [(100, 1, 1_000, 7_000)]

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
        assert state.get_process_lifetimes() == [(100, 1, 500, 1_000)]

    def test_both_ends_always_carry_the_same_pids(self) -> None:
        """One call gives a pid both a start and an end, so no pid can
        land in one dict and not the other -- which is what lets
        ``get_process_lifetimes`` index the start dict while iterating
        the end one."""
        state = PerfettoTrackState()
        for pid, ts in ((100, 1_000), (200, 2_000), (100, 3_000)):
            state.update_process_lifetime(pid, ts)
        assert state._process_lifetime_start.keys() == state._process_lifetime_end.keys()
        assert sorted(state.get_process_lifetimes()) == [(100, 1, 1_000, 3_000), (200, 1, 2_000, 2_000)]

    def test_get_returns_every_span_regardless_of_order(self) -> None:
        """Order is not part of the contract -- ``_clip_spans_to_laminar``
        sorts what it needs -- so this pins the contents only."""
        state = PerfettoTrackState()
        # Inserted out of order, with a tie on start ts
        # between pids 300 and 100.
        for pid, start, end in (
            (200, 2_000, 3_000),
            (300, 1_000, 4_000),
            (100, 1_000, 9_000),
        ):
            state.update_process_lifetime(pid, start)
            state.update_process_lifetime(pid, end)
        assert sorted(state.get_process_lifetimes()) == [
            (100, 1, 1_000, 9_000),
            (200, 1, 2_000, 3_000),
            (300, 1, 1_000, 4_000),
        ]

    def test_get_does_not_drain(self) -> None:
        """Reading spans has no side effect: the once-per-trace contract
        is ``finalize_perfetto_packets``' flag, not a drain here."""
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 1_000)
        assert state.get_process_lifetimes() == [(100, 1, 1_000, 1_000)]
        assert state.get_process_lifetimes() == [(100, 1, 1_000, 1_000)]
        assert state.has_process_lifetime(100, 1)
        assert state.get_process_lifetime_start_ts(100, 1) == 1_000

    def test_a_pid_reported_live_throughout_keeps_one_span(self) -> None:
        """Every tick reports the pid, so nothing closes and the span
        stays a plain min/max. This is every pid of a run with no
        reuse."""
        state = PerfettoTrackState()
        for ts in (1_000, 2_000, 3_000):
            state.observe_process_liveness({100}, ts)
        assert state.get_process_lifetimes() == [(100, 1, 1_000, 3_000)]

    def test_a_pid_absent_from_a_report_and_back_later_is_a_second_process(self) -> None:
        """The rule the whole epoch rests on. Pid 100 answers the first
        tick, is missing from the second, and answers the third: the
        operating system handed the number to something else, and the
        two processes get a span each rather than one span across the
        gap they never both existed in."""
        state = PerfettoTrackState()
        state.observe_process_liveness({100, 200}, 1_000)
        state.observe_process_liveness({200}, 2_000)
        state.observe_process_liveness({100, 200}, 3_000)
        assert sorted(state.get_process_lifetimes()) == [
            (100, 1, 1_000, 1_000),
            (100, 2, 3_000, 3_000),
            (200, 1, 1_000, 3_000),
        ]

    def test_an_event_after_a_close_belongs_to_the_next_process(self) -> None:
        """A record is evidence like a tick is, so the process gcmon
        polls after a pid was handed on opens its own span whether the
        first thing it produces is a liveness report or a GC event."""
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 1_000)
        state.observe_process_liveness({200}, 2_000)
        state.update_process_lifetime(100, 3_000)
        state.update_process_lifetime(100, 4_000)
        assert sorted(state.get_process_lifetimes()) == [
            (100, 1, 1_000, 1_000),
            (100, 2, 3_000, 4_000),
            (200, 1, 2_000, 2_000),
        ]

    def test_evidence_older_than_a_closed_span_belongs_to_it(self) -> None:
        """A pid pruned from the process tree loses its read cursors, so
        the process that claims it next re-exports records the one before
        it already produced. Those records date from the first process
        and are filed under it, whatever order they arrive in."""
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 1_000)
        state.observe_process_liveness({100}, 2_000)
        state.observe_process_liveness({200}, 3_000)
        # Read again after the prune: the old records first, then one the
        # first process never produced.
        state.update_process_lifetime(100, 1_500)
        state.update_process_lifetime(100, 500)
        state.update_process_lifetime(100, 5_000)
        # And one more straggler, after the second span is open.
        state.update_process_lifetime(100, 1_800)
        assert sorted(state.get_process_lifetimes()) == [
            (100, 1, 500, 2_000),
            (100, 2, 5_000, 5_000),
            (200, 1, 3_000, 3_000),
        ]

    def test_two_spans_on_one_pid_never_overlap(self) -> None:
        """Two processes cannot hold one pid at once, and two slices of
        one name cannot be open at once either: a named END would have
        two BEGINs to choose between."""
        state = PerfettoTrackState()
        for ts in (1_000, 2_000):
            state.observe_process_liveness({100}, ts)
        state.observe_process_liveness({200}, 3_000)
        for ts in (500, 1_500, 2_500, 4_000):
            state.update_process_lifetime(100, ts)
        spans = sorted((start, end) for pid, _pid_epoch, start, end in state.get_process_lifetimes() if pid == 100)
        assert len(spans) == 2
        assert spans[0][1] < spans[1][0]

    def test_a_pid_that_never_comes_back_keeps_one_span(self) -> None:
        """Closing a span is not what splits it. A process that exits and
        leaves the pid unclaimed reads exactly as it did before epochs
        existed."""
        state = PerfettoTrackState()
        state.observe_process_liveness({100, 200}, 1_000)
        state.observe_process_liveness({200}, 2_000)
        assert sorted(state.get_process_lifetimes()) == [(100, 1, 1_000, 1_000), (200, 1, 1_000, 2_000)]

    def test_each_process_opens_its_own_track(self) -> None:
        """A process track is per process, so the one that took the pid
        over opens where it started and ranks there rather than
        inheriting its predecessor's place."""
        state = PerfettoTrackState()
        state.observe_process_liveness({100}, 5_000)
        state.observe_process_liveness({200}, 6_000)
        state.observe_process_liveness({100, 200}, 7_000)
        assert state.get_process_lifetime_start_ts(100, 1) == 5_000
        assert state.get_process_lifetime_start_ts(100, 2) == 7_000
        assert state.get_process_track_ranks() == {(100, 1): 0, (200, 1): 1, (100, 2): 2}

    def test_process_lifetime_emitted_flag(self) -> None:
        state = PerfettoTrackState()
        assert not state.has_process_lifetime_emitted()
        state.mark_process_lifetime_emitted()
        assert state.has_process_lifetime_emitted()


class TestEpochAt:
    """Which process held a pid at an instant, asked without changing
    anything.

    The span accumulator answers the same question while folding evidence
    in, and opens a span as a side effect of answering. The descriptor
    side needs the answer alone: it draws a row for the process an event
    belongs to, and drawing must not invent a process.
    """

    def test_a_pid_with_no_evidence_is_the_first_process(self) -> None:
        """Nothing has held the pid yet, so whatever arrives next is the
        first to."""
        state = PerfettoTrackState()
        assert state.epoch_at(100, 1_000) == 1

    def test_an_instant_inside_the_open_span_is_the_open_process(self) -> None:
        state = PerfettoTrackState()
        state.observe_process_liveness({100}, 1_000)
        state.observe_process_liveness({100}, 3_000)
        assert state.epoch_at(100, 2_000) == 1
        assert state.epoch_at(100, 4_000) == 1

    def test_an_instant_after_a_closed_span_is_the_next_process(self) -> None:
        """The pid was handed on and nothing has claimed it yet. An event
        at this instant would open the second span, so the answer names
        the process that would open it."""
        state = PerfettoTrackState()
        state.observe_process_liveness({100}, 1_000)
        state.observe_process_liveness({200}, 2_000)
        assert state.epoch_at(100, 3_000) == 2

    def test_an_instant_inside_a_closed_span_is_that_process(self) -> None:
        """A pid pruned from the process tree loses its read cursors, so
        the next process re-exports records the one before it produced.
        They date from the first process wherever they arrive."""
        state = PerfettoTrackState()
        state.observe_process_liveness({100}, 1_000)
        state.observe_process_liveness({100}, 2_000)
        state.observe_process_liveness({200}, 3_000)
        state.update_process_lifetime(100, 5_000)
        assert state.epoch_at(100, 1_500) == 1
        assert state.epoch_at(100, 5_000) == 2

    def test_asking_opens_no_process_and_widens_no_span(self) -> None:
        """The whole point of a second method. Ask past the end of a
        closed span, twice, and the trace still holds one process on that
        pid until something is folded in."""
        state = PerfettoTrackState()
        state.observe_process_liveness({100}, 1_000)
        state.observe_process_liveness({200}, 2_000)
        before = sorted(state.get_process_lifetimes())

        assert state.epoch_at(100, 9_000) == 2
        assert state.epoch_at(100, 9_000) == 2

        assert sorted(state.get_process_lifetimes()) == before
        state.update_process_lifetime(100, 9_000)
        assert sorted(state.get_process_lifetimes()) == sorted([*before, (100, 2, 9_000, 9_000)])

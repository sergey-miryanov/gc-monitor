"""Tests for process ordering: root descriptor, ``sibling_order_rank``
and ``start_timestamp_ns``.

The code under test lives in ``perfetto_format``, but the subject is
ADR-0011's, which is why these sit apart from the convert-core tests.
"""

from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import (
    TrackDescriptor,
)

from gcmon.exporters.perfetto_format import convert_trace_events_to_perfetto
from gcmon.exporters.perfetto_track_state import PerfettoTrackState
from gcmon.exporters.trace_converter import convert_item_to_trace_format
from gcmon.model.data import GCStatsInfo
from gcmon.model.trace_event import Instant, ProcessTrack, TraceEvent
from tests.exporters.perfetto_helpers import (
    parse_track_descriptor,
)


def _process_descriptor_fields_for_pid(
    descriptors: list[bytes],
    pid: int,
) -> list[TrackDescriptor]:
    """Return the ``TrackDescriptor`` protos for the process
    descriptor of *pid* (i.e. a TrackDescriptor with a ``process``
    sub-message carrying the matching pid). Returns an empty list if
    no matching descriptor exists.
    """
    matched: list[TrackDescriptor] = []
    for d in descriptors:
        td = parse_track_descriptor(d)
        if td is None:
            continue
        if td.HasField("process") and td.process.pid == pid:
            matched.append(td)
    return matched


def _root_descriptor_fields(descriptors: list[bytes]) -> list[TrackDescriptor]:
    """Return the ``TrackDescriptor`` protos for the root
    descriptor (the one with ``uuid = 0``)."""
    matched: list[TrackDescriptor] = []
    for d in descriptors:
        td = parse_track_descriptor(d)
        if td is None:
            continue
        if td.uuid == 0:
            matched.append(td)
    return matched


class TestProcessOrderingByFirstTs:
    """Wire-level tests for the root descriptor and per-process
    ``sibling_order_rank`` derived from the first event timestamp."""

    def test_root_descriptor_present_with_explicit_ordering(self) -> None:
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            Instant(ProcessTrack(100), "start", ts=5_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        roots = _root_descriptor_fields(descriptors)
        assert len(roots) == 1
        td = roots[0]
        assert td.process_ordering == 1
        assert td.thread_ordering == 1
        assert not td.HasField("name")
        assert not td.HasField("process")
        assert not td.HasField("thread")
        assert not td.HasField("counter")
        assert not td.HasField("parent_uuid")
        assert not td.HasField("child_ordering")

    def test_root_descriptor_emitted_exactly_once_across_calls(self) -> None:
        state = PerfettoTrackState()
        events1: list[TraceEvent] = [
            Instant(ProcessTrack(100), "first", ts=1_000),
        ]
        events2: list[TraceEvent] = [
            Instant(ProcessTrack(200), "second", ts=2_000),
        ]
        d1, _ = convert_trace_events_to_perfetto(events1, state, sequence_id=1)
        d2, _ = convert_trace_events_to_perfetto(events2, state, sequence_id=1)
        total_roots = len(_root_descriptor_fields(d1)) + len(_root_descriptor_fields(d2))
        assert total_roots == 1, f"expected one root descriptor total, got {total_roots}"

    def test_root_descriptor_not_emitted_for_empty_input(self) -> None:
        state = PerfettoTrackState()
        descriptors, packets = convert_trace_events_to_perfetto([], state, sequence_id=1)
        assert descriptors == []
        assert packets == []

    def test_process_descriptor_carries_sibling_order_rank_by_first_ts(self) -> None:
        """Pid with earlier first ts gets the smaller rank."""
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            Instant(ProcessTrack(1), "ev1", ts=2_000),
            Instant(ProcessTrack(2), "ev2", ts=1_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        ranks = {
            pid: td.sibling_order_rank for pid in (1, 2) for td in _process_descriptor_fields_for_pid(descriptors, pid)
        }
        assert ranks == {1: 1, 2: 0}, f"unexpected rank assignment: {ranks}"

    def test_sibling_order_rank_ties_broken_by_pid(self) -> None:
        """When two pids share the same first event ts, ranks follow
        ascending pid (deterministic)."""
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            Instant(ProcessTrack(2), "ev", ts=1_000),
            Instant(ProcessTrack(1), "ev", ts=1_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        ranks = {
            pid: td.sibling_order_rank for pid in (1, 2) for td in _process_descriptor_fields_for_pid(descriptors, pid)
        }
        assert ranks == {1: 0, 2: 1}, f"expected pid-ascending tiebreak; got {ranks}"

    def test_rank_follows_the_first_event_and_not_the_descriptor_order(self) -> None:
        """The pid whose descriptor goes out first is not the pid that
        ranks first: the rank comes from the earliest event, and the
        descriptors follow whichever event named a track first."""
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            Instant(ProcessTrack(100), "late", ts=5_000),
            Instant(ProcessTrack(200), "early", ts=1_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        ranks = {
            pid: td.sibling_order_rank
            for pid in (100, 200)
            for td in _process_descriptor_fields_for_pid(descriptors, pid)
        }
        assert ranks == {100: 1, 200: 0}, f"unexpected rank assignment: {ranks}"

    def test_sibling_order_rank_uses_ts_start_for_gc_stats(self) -> None:
        """For ``TGCStatsInfo`` events, the first event ts is the
        ``ts_start`` (the earliest emitted event for that pause)."""
        state = PerfettoTrackState()
        item1 = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        events: list[TraceEvent] = [
            Instant(ProcessTrack(2), "ev", ts=2_000),
            *convert_item_to_trace_format(1, item1),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        ranks = {
            pid: td.sibling_order_rank for pid in (1, 2) for td in _process_descriptor_fields_for_pid(descriptors, pid)
        }
        assert ranks == {1: 1, 2: 0}, f"unexpected rank assignment: {ranks}"

    def test_sibling_order_rank_unchanged_when_input_pid_order_swapped(self) -> None:
        """Reordering the input pids (with the same first-ts values)
        must produce identical rank assignments."""

        def _make_events(ordered_pids: list[int]) -> list[TraceEvent]:
            ts_map = {1: 2_000, 2: 1_000}
            return [ev for pid in ordered_pids for ev in (Instant(ProcessTrack(pid), "ev", ts=ts_map[pid]),)]

        s1 = PerfettoTrackState()
        d1, _ = convert_trace_events_to_perfetto(_make_events([1, 2]), s1, sequence_id=1)
        s2 = PerfettoTrackState()
        d2, _ = convert_trace_events_to_perfetto(_make_events([2, 1]), s2, sequence_id=1)
        ranks1 = {pid: td.sibling_order_rank for pid in (1, 2) for td in _process_descriptor_fields_for_pid(d1, pid)}
        ranks2 = {pid: td.sibling_order_rank for pid in (1, 2) for td in _process_descriptor_fields_for_pid(d2, pid)}
        assert ranks1 == ranks2 == {1: 1, 2: 0}

    def test_rank_persists_across_batches(self) -> None:
        """First-ts recorded in one batch must be remembered when
        computing ranks in a later batch (multi-flush invariant)."""
        s = PerfettoTrackState()
        d1, _ = convert_trace_events_to_perfetto(
            [Instant(ProcessTrack(1), "a", ts=1_000)],
            s,
            sequence_id=1,
        )
        d2, _ = convert_trace_events_to_perfetto(
            [Instant(ProcessTrack(2), "b", ts=5_000)],
            s,
            sequence_id=1,
        )
        # The pre-scan also re-records for batch 2, but the first-ts
        # for pid 1 from batch 1 is preserved (record_first_event_ts
        # only sets the first ts for a pid). Pid 1 should still get
        # rank 0 (ts=1_000) and pid 2 rank 1 (ts=5_000).
        ranks = {
            pid: td.sibling_order_rank
            for descriptors in (d1, d2)
            for pid in (1, 2)
            for td in _process_descriptor_fields_for_pid(descriptors, pid)
        }
        assert ranks == {1: 0, 2: 1}, f"unexpected rank assignment: {ranks}"

    def test_process_descriptor_writes_start_timestamp_ns(self) -> None:
        """Each process descriptor carries ``start_timestamp_ns``
        set to the first non-meta event ts for the pid (nanoseconds).
        The Perfetto UI uses this to align the process track with the
        process's actual start time.
        """
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            Instant(ProcessTrack(100), "start", ts=5_000),
            Instant(ProcessTrack(200), "start", ts=1_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        start_ts: dict[int, int] = {}
        for pid in (100, 200):
            tds = _process_descriptor_fields_for_pid(descriptors, pid)
            assert len(tds) == 1
            start_ts[pid] = tds[0].process.start_timestamp_ns
        assert start_ts == {100: 5_000, 200: 1_000}

    def test_start_timestamp_ns_uses_ts_start_for_gc_stats(self) -> None:
        """For ``TGCStatsInfo`` events, the first-ts (and therefore
        ``start_timestamp_ns``) is the ``ts_start`` of the first GC
        pause, not the ``ts_stop`` or any sub-event ts."""
        from gcmon.model.data import GCStatsInfo

        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        events: list[TraceEvent] = [
            Instant(ProcessTrack(2), "ev", ts=2_000),
            *convert_item_to_trace_format(1, item),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        start_ts: dict[int, int] = {}
        for pid in (1, 2):
            tds = _process_descriptor_fields_for_pid(descriptors, pid)
            start_ts[pid] = tds[0].process.start_timestamp_ns
        assert start_ts == {1: 3_000, 2: 2_000}

    def test_start_timestamp_ns_persists_across_batches(self) -> None:
        """First-ts recorded in one batch must be remembered when
        the process descriptor is emitted in a later batch."""
        s = PerfettoTrackState()
        d1, _ = convert_trace_events_to_perfetto(
            [Instant(ProcessTrack(1), "a", ts=1_000)],
            s,
            sequence_id=1,
        )
        d2, _ = convert_trace_events_to_perfetto(
            [Instant(ProcessTrack(2), "b", ts=5_000)],
            s,
            sequence_id=1,
        )
        # Pid 1 was seen in batch 1; pid 2 in batch 2.
        tds_1 = _process_descriptor_fields_for_pid(d1, 1)
        assert len(tds_1) == 1
        assert tds_1[0].process.start_timestamp_ns == 1_000
        tds_2 = _process_descriptor_fields_for_pid(d2, 2)
        assert len(tds_2) == 1
        assert tds_2[0].process.start_timestamp_ns == 5_000


class TestAProcessThatTookAPidOver:
    """A pid held twice draws a descriptor per process, each stamped and
    ranked where its own process started.

    Before this there was one descriptor for both, stamped at the first
    process's start: a row the UI sorted into a place the process it drew
    did not yet exist in. See ADR-0011.
    """

    def _descriptors_over_a_handover(self) -> list[TrackDescriptor]:
        """Pid 100 answers a tick, misses one, and is back with an event
        of its own."""
        state = PerfettoTrackState()
        state.observe_process_liveness({100}, 1_000)
        first, _ = convert_trace_events_to_perfetto(
            [Instant(ProcessTrack(100), "start", ts=1_500)],
            state,
            sequence_id=1,
        )
        state.observe_process_liveness(set(), 2_000)
        second, _ = convert_trace_events_to_perfetto(
            [Instant(ProcessTrack(100), "start", ts=3_000)],
            state,
            sequence_id=1,
        )
        return _process_descriptor_fields_for_pid([*first, *second], 100)

    def test_each_process_gets_a_descriptor(self) -> None:
        assert len(self._descriptors_over_a_handover()) == 2

    def test_each_descriptor_is_named_for_its_process(self) -> None:
        """The same string the process's span on the Processes track
        takes, so the two match by eye."""
        names = [td.name for td in self._descriptors_over_a_handover()]
        assert names == ["Process 100", "Process 100#2"]

    def test_each_descriptor_is_stamped_where_its_process_started(self) -> None:
        stamps = [td.process.start_timestamp_ns for td in self._descriptors_over_a_handover()]
        assert stamps == [1_000, 3_000]

    def test_each_descriptor_ranks_where_its_process_started(self) -> None:
        ranks = [td.sibling_order_rank for td in self._descriptors_over_a_handover()]
        assert ranks == [0, 1]

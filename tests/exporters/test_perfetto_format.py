"""Tests for Perfetto protobuf message builders and conversion."""

import random

import pytest
from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import (
    ThreadDescriptor,
    Trace,
    TracePacket,
    TrackDescriptor,
    TrackEvent,
)

from gcmon.data import GCStatsInfo
from gcmon.exporters.perfetto_format import (
    PerfettoTrackState,
    TrackEventType,
    build_trace,
    build_trace_packet,
    build_track_descriptor,
    build_track_event,
    convert_trace_events_to_perfetto,
    finalize_perfetto_packets,
)
from gcmon.exporters.perfetto_process_lifetime import _clip_spans_to_laminar
from gcmon.exporters.trace_converter import convert_item_to_trace_format
from gcmon.trace_event import TraceEvent, counter_event, instant_event, process_meta, thread_meta

# Name of the synthetic marker emitted on the process track so the
# cmdline description is always visible in the Perfetto UI. Must match
# ``_START_PROCESS_INSTANT_NAME`` in ``gcmon.exporters.perfetto_format``.
_START_PROCESS_MARKER_NAME: str = "Start Process"

# Name of the shared top-level Perfetto track that holds one slice per
# pid spanning the first-to-last non-meta event timestamps for that
# pid. Must match ``_PROCESS_LIFETIME_TRACK_NAME`` in
# ``gcmon.exporters.perfetto_format``.
_PROCESS_LIFETIME_TRACK_NAME: str = "Processes"


def _convert_item(
    pid: int,
    item: GCStatsInfo,
    state: PerfettoTrackState,
    sequence_id: int = 1,
) -> tuple[list[bytes], list[bytes]]:
    gc_events = convert_item_to_trace_format(pid, item)
    meta: list[TraceEvent] = [
        process_meta(pid, f"Process {pid}"),
        thread_meta(pid, item.iid, f"Thread {item.iid}"),
    ]
    descriptors, packets = convert_trace_events_to_perfetto(
        meta + gc_events,
        state,
        sequence_id,
    )
    packets.extend(finalize_perfetto_packets(state, sequence_id))
    return descriptors, packets


def _convert_items(
    items: list[tuple[int, GCStatsInfo]],
    state: PerfettoTrackState,
    sequence_id: int = 1,
) -> tuple[list[bytes], list[bytes], list[bytes]]:
    """Convert each ``(pid, item)`` as its own batch, then finalize once,
    the way ``ProtobufEventEncoder`` does across flushes.

    Returns ``(descriptors, convert_packets, closeout_packets)`` so a
    test can tell what the convert passes emitted from what the single
    closeout emitted.
    """
    descriptors: list[bytes] = []
    packets: list[bytes] = []
    for pid, item in items:
        meta: list[TraceEvent] = [
            process_meta(pid, f"Process {pid}"),
            thread_meta(pid, item.iid, f"Thread {item.iid}"),
        ]
        batch_desc, batch_packets = convert_trace_events_to_perfetto(
            meta + convert_item_to_trace_format(pid, item),
            state,
            sequence_id,
        )
        descriptors.extend(batch_desc)
        packets.extend(batch_packets)
    return descriptors, packets, finalize_perfetto_packets(state, sequence_id)


def _lifetime_slices(
    packets: list[bytes],
    lifetime_uuid: int,
) -> list[tuple[int, int, str, dict[str, str | int]]]:
    """Return ``[(ts, type, name, annotations), ...]`` for the slice
    events on the ``Processes`` track, in packet order."""
    out: list[tuple[int, int, str, dict[str, str | int]]] = []
    for p in packets:
        packet = TracePacket()
        packet.ParseFromString(p)
        if not packet.HasField("track_event"):
            continue
        track_event = packet.track_event
        if track_event.track_uuid != lifetime_uuid:
            continue
        if track_event.type not in (
            TrackEvent.Type.TYPE_SLICE_BEGIN,
            TrackEvent.Type.TYPE_SLICE_END,
        ):
            continue
        annotations: dict[str, str | int] = {}
        for ann in track_event.debug_annotations:
            annotations[ann.name] = ann.string_value if ann.HasField("string_value") else ann.int_value
        out.append((packet.timestamp, track_event.type, track_event.name or "", annotations))
    return out


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


def _finalize_spans(
    spans: list[tuple[int, int, int]],
) -> tuple[dict[int, tuple[int, int]], dict[int, tuple[int, int]]]:
    """Run *spans* -- ``[(pid, start, end), ...]`` -- through
    ``finalize_perfetto_packets`` and decode the result.

    Returns ``({pid: (ts, end_ts)}, {pid: (real_start_ts, real_end_ts)})``:
    the span each slice *draws*, and the span it *records*. Every pid
    with a span appears in both -- nothing is ever dropped -- so the two
    dicts always have the same keys. Also asserts that the emitted
    BEGIN/END packets nest as a well-formed stack, which is what the
    trace processor requires of a single track.
    """
    state = PerfettoTrackState()
    for pid, start, end in spans:
        state.mark_pid(pid)
        state.update_process_lifetime(pid, start, extends_end=True)
        state.update_process_lifetime(pid, end, extends_end=True)
    packets = finalize_perfetto_packets(state, sequence_id=1)
    lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

    intervals: dict[int, tuple[int, int]] = {}
    real: dict[int, tuple[int, int]] = {}
    open_stack: list[tuple[str, int]] = []
    for ts, event_type, name, annotations in _lifetime_slices(packets, lifetime_uuid):
        if event_type == TrackEventType.SLICE_BEGIN:
            open_stack.append((name, ts))
            real[int(name.removeprefix("Process "))] = (
                int(annotations["real_start_ts"]),
                int(annotations["real_end_ts"]),
            )
        else:
            assert open_stack, f"slice END for {name!r} at ts {ts} with nothing open"
            open_name, open_ts = open_stack.pop()
            assert open_name == name, (
                f"slice END for {name!r} closed {open_name!r}: the track is not a well-formed stack"
            )
            intervals[int(name.removeprefix("Process "))] = (open_ts, ts)
    assert not open_stack, f"unclosed slices left open: {open_stack}"
    assert intervals.keys() == real.keys()
    return intervals, real


def _assert_laminar(intervals: dict[int, tuple[int, int]]) -> None:
    """Assert that no two intervals cross: each pair is either disjoint
    or one strictly contains the other."""
    items = sorted(intervals.items(), key=lambda kv: kv[1])
    for i, (pid_a, (start_a, end_a)) in enumerate(items):
        for pid_b, (start_b, end_b) in items[i + 1 :]:
            disjoint = end_a < start_b or end_b < start_a
            nested = (start_a <= start_b and end_b <= end_a) or (start_b <= start_a and end_a <= end_b)
            assert disjoint or nested, f"pids {pid_a} [{start_a}, {end_a}] and {pid_b} [{start_b}, {end_b}] cross"


def _sorted_for_clip(spans: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """Sort *spans* the way ``pop_process_lifetimes`` does, which is the
    order ``_clip_spans_to_laminar`` documents as its precondition."""
    return sorted(spans, key=lambda s: (s[1], -s[2], s[0]))


class TestClipSpansToLaminar:
    """Direct tests for the ``_clip_spans_to_laminar`` sweep.

    It is a pure function, so it can be exercised without building
    packets. ``TestProcessLifetimeLaminarClipping`` below covers the same
    ground through ``finalize_perfetto_packets`` and additionally checks
    that the emitted BEGIN/END packets form a well-formed stack; these
    tests pin the sweep's own contract, including the parts the packet
    view cannot see, such as output ordering.
    """

    def test_no_spans(self) -> None:
        assert _clip_spans_to_laminar([]) == []

    def test_single_span_is_unchanged(self) -> None:
        assert _clip_spans_to_laminar([(100, 500, 9_000)]) == [(100, 500, 9_000, 500, 9_000)]

    def test_disjoint_spans_are_unchanged(self) -> None:
        """The first span has closed before the second opens, so the
        sweep pops it and clips nothing."""
        spans = [(100, 500, 1_000), (200, 5_000, 9_000)]
        assert _clip_spans_to_laminar(spans) == [
            (100, 500, 1_000, 500, 1_000),
            (200, 5_000, 9_000, 5_000, 9_000),
        ]

    def test_nested_span_is_unchanged(self) -> None:
        """A span contained by the one still open stops the walk, which
        is the common shape of a parent outliving its child."""
        spans = [(100, 500, 9_000), (200, 1_000, 5_000)]
        assert _clip_spans_to_laminar(spans) == [
            (100, 500, 9_000, 500, 9_000),
            (200, 1_000, 5_000, 1_000, 5_000),
        ]

    def test_crossing_clips_the_outer_end(self) -> None:
        """The whole point: the earlier span's end is pulled back to one
        nanosecond before the later one starts."""
        spans = [(100, 500, 1_500), (200, 1_000, 5_000)]
        assert _clip_spans_to_laminar(spans) == [
            (100, 500, 999, 500, 1_500),
            (200, 1_000, 5_000, 1_000, 5_000),
        ]

    def test_touching_is_treated_as_crossing(self) -> None:
        """``A.end == B.start`` is clipped too: the relative order of an
        END and a BEGIN sharing a timestamp is not ours to control."""
        spans = [(100, 500, 1_000), (200, 1_000, 5_000)]
        assert _clip_spans_to_laminar(spans) == [
            (100, 500, 999, 500, 1_000),
            (200, 1_000, 5_000, 1_000, 5_000),
        ]

    def test_equal_starts_always_nest(self) -> None:
        """Given the required sort, equal starts arrive longest-first and
        can never cross, which is what keeps ``start - 1`` from landing
        before the clipped span's own start."""
        spans = _sorted_for_clip([(100, 500, 1_000), (200, 500, 9_000)])
        assert _clip_spans_to_laminar(spans) == [
            (200, 500, 9_000, 500, 9_000),
            (100, 500, 1_000, 500, 1_000),
        ]

    def test_walk_pops_every_span_already_closed(self) -> None:
        """Two spans are open and nested when a third starts after both
        have ended; the sweep unwinds the whole stack in one walk and
        clips neither."""
        spans = [(100, 0, 100), (200, 10, 20), (300, 200, 300)]
        assert _clip_spans_to_laminar(spans) == [
            (100, 0, 100, 0, 100),
            (200, 10, 20, 10, 20),
            (300, 200, 300, 200, 300),
        ]

    def test_one_span_crossed_by_two_later_spans(self) -> None:
        """The sweep is not a pairwise check of neighbours. Pid 200 nests
        inside pid 100, so comparing only adjacent spans would stop there
        and never notice that pid 300 crosses pid 100."""
        spans = [(100, 500, 5_000), (200, 1_000, 2_000), (300, 3_000, 9_000)]
        assert _clip_spans_to_laminar(spans) == [
            (100, 500, 2_999, 500, 5_000),
            (200, 1_000, 2_000, 1_000, 2_000),
            (300, 3_000, 9_000, 3_000, 9_000),
        ]

    def test_chain_of_crossings_clips_each_in_turn(self) -> None:
        spans = [(100, 0, 100), (200, 10, 200), (300, 20, 300)]
        assert _clip_spans_to_laminar(spans) == [
            (100, 0, 9, 0, 100),
            (200, 10, 19, 10, 200),
            (300, 20, 300, 20, 300),
        ]

    def test_clip_can_reduce_a_span_to_zero_length(self) -> None:
        """A crossing span starting one nanosecond later leaves nothing
        to draw. The span is still returned -- dropping it is the
        caller's decision, and the caller does not make it."""
        spans = [(100, 500, 5_000), (200, 501, 9_000)]
        assert _clip_spans_to_laminar(spans) == [
            (100, 500, 500, 500, 5_000),
            (200, 501, 9_000, 501, 9_000),
        ]

    def test_zero_length_input_survives(self) -> None:
        """A pid observed at a single instant arrives zero-length and is
        passed through, not discarded."""
        spans = _sorted_for_clip([(100, 500, 500), (200, 500, 9_000)])
        assert _clip_spans_to_laminar(spans) == [
            (200, 500, 9_000, 500, 9_000),
            (100, 500, 500, 500, 500),
        ]

    def test_output_preserves_input_order(self) -> None:
        """The sweep runs over a stack but the result is returned in the
        order it was given, so the caller can emit spans in sorted order
        without re-sorting."""
        spans = [(100, 0, 100), (200, 10, 200), (300, 20, 300)]
        assert [row[0] for row in _clip_spans_to_laminar(spans)] == [100, 200, 300]

    @pytest.mark.parametrize("seed", range(50))
    def test_invariants_hold_for_random_spans(self, seed: int) -> None:
        """Whatever goes in: the result is laminar, every span survives,
        its start and observed span are untouched, and its end only ever
        moves inwards -- never past its own start, and never outwards."""
        rng = random.Random(seed)
        spans = _sorted_for_clip(
            [
                (pid, start, start + rng.randrange(0, 2_000))
                for pid in range(100, 100 + rng.randint(2, 12))
                for start in (rng.randrange(0, 2_000),)
            ]
        )
        clipped = _clip_spans_to_laminar(spans)

        assert [row[0] for row in clipped] == [pid for pid, _s, _e in spans]
        for (pid, start, end, real_start, real_end), original in zip(clipped, spans, strict=True):
            assert (pid, real_start, real_end) == original, "the observed span is passed through"
            assert start == real_start, "a start is never moved"
            assert real_start <= end <= real_end, "an end only ever moves inwards"
        _assert_laminar({pid: (start, end) for pid, start, end, _rs, _re in clipped})


class TestProcessLifetimeLaminarClipping:
    """``finalize_perfetto_packets`` clips spans so that the shared
    ``Processes`` track only ever holds disjoint or strictly nested
    slices. Slices on one Perfetto track are a stack, so a crossing pair
    cannot be expressed: the trace processor closes both at the earlier
    END and discards the later one as a ``misplaced_end_event``."""

    def test_crossing_clips_the_earlier_end(self) -> None:
        intervals, real = _finalize_spans([(100, 500, 1_500), (200, 1_000, 5_000)])
        assert intervals == {100: (500, 999), 200: (1_000, 5_000)}
        assert real == {100: (500, 1_500), 200: (1_000, 5_000)}
        _assert_laminar(intervals)

    def test_containment_is_left_alone(self) -> None:
        """A parent outliving its child nests correctly, so the common
        multi-process shape costs nothing."""
        intervals, real = _finalize_spans([(100, 500, 9_000), (200, 1_000, 5_000)])
        assert intervals == {100: (500, 9_000), 200: (1_000, 5_000)}
        assert real == intervals
        _assert_laminar(intervals)

    def test_disjoint_is_left_alone(self) -> None:
        intervals, real = _finalize_spans([(100, 500, 1_000), (200, 5_000, 9_000)])
        assert intervals == {100: (500, 1_000), 200: (5_000, 9_000)}
        assert real == intervals

    def test_touching_counts_as_crossing(self) -> None:
        """``A.end == B.start`` is clipped too: the relative order of an
        END and a BEGIN sharing a timestamp is not ours to control."""
        intervals, real = _finalize_spans([(100, 500, 1_000), (200, 1_000, 5_000)])
        assert intervals == {100: (500, 999), 200: (1_000, 5_000)}
        assert real == {100: (500, 1_000), 200: (1_000, 5_000)}

    def test_equal_starts_nest_longest_first(self) -> None:
        """Spans sharing a start can never cross, so none is clipped."""
        intervals, real = _finalize_spans([(100, 500, 1_000), (200, 500, 9_000)])
        assert intervals == {200: (500, 9_000), 100: (500, 1_000)}
        assert real == intervals
        _assert_laminar(intervals)

    def test_one_span_crossed_by_two_later_spans(self) -> None:
        """The clip is a sweep, not a pairwise comparison of neighbours.

        Pid 100 spans everything below. Pid 200 nests inside it, so a
        check that only compared each span with the next one would stop
        there and never notice that pid 300 crosses pid 100.
        """
        intervals, real = _finalize_spans(
            [(100, 500, 5_000), (200, 1_000, 2_000), (300, 3_000, 9_000)],
        )
        assert intervals == {100: (500, 2_999), 200: (1_000, 2_000), 300: (3_000, 9_000)}
        assert real == {100: (500, 5_000), 200: (1_000, 2_000), 300: (3_000, 9_000)}
        _assert_laminar(intervals)

    def test_single_instant_span_is_still_drawn(self) -> None:
        """A pid observed at a single instant gets a zero-duration slice
        rather than nothing. It is the only place the track records that
        the process existed, and omission is the one distortion a reader
        has no way to notice."""
        intervals, real = _finalize_spans([(100, 500, 500)])
        assert intervals == {100: (500, 500)}
        assert real == {100: (500, 500)}

    def test_span_clipped_to_zero_is_still_drawn(self) -> None:
        """Pid 100 is clipped to ``[500, 500]`` by pid 200 starting one
        nanosecond later. Nothing is left to draw, but the slice is
        emitted anyway and its annotations still carry the real 4.5us
        span."""
        intervals, real = _finalize_spans([(100, 500, 5_000), (200, 501, 9_000)])
        assert intervals == {100: (500, 500), 200: (501, 9_000)}
        assert real == {100: (500, 5_000), 200: (501, 9_000)}
        _assert_laminar(intervals)

    def test_no_spans_emits_nothing(self) -> None:
        assert _finalize_spans([]) == ({}, {})

    def test_pid_without_process_descriptor_is_skipped(self) -> None:
        """A span is only drawn for a pid that reached ``mark_pid``, i.e.
        one whose ``ProcessMeta`` was seen."""
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 500, extends_end=True)
        state.update_process_lifetime(100, 5_000, extends_end=True)
        assert finalize_perfetto_packets(state, sequence_id=1) == []

    @pytest.mark.parametrize("seed", range(25))
    def test_output_is_always_laminar(self, seed: int) -> None:
        """Whatever spans go in, no two slices come out crossing, every
        BEGIN is closed by its own END, every pid keeps a slice, and
        every slice reports its observed span truthfully."""
        rng = random.Random(seed)
        spans: list[tuple[int, int, int]] = []
        for pid in range(100, 100 + rng.randint(2, 12)):
            start = rng.randrange(0, 2_000)
            spans.append((pid, start, start + rng.randrange(0, 2_000)))
        intervals, real = _finalize_spans(spans)
        _assert_laminar(intervals)
        assert intervals.keys() == {pid for pid, _s, _e in spans}, "no pid is ever dropped"
        for pid, (start_ts, end_ts) in intervals.items():
            original = next((s, e) for p, s, e in spans if p == pid)
            assert real[pid] == original, "the recorded span is the observed one"
            assert start_ts == original[0], "a span's start is never moved"
            assert start_ts <= end_ts <= original[1], "an end is only ever pulled in"


class TestBuildTrackDescriptor:
    def test_process_descriptor(self) -> None:
        data = build_track_descriptor(uuid=100, name="Process 100", pid=100)
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert descriptor.uuid == 100
        assert descriptor.name == "Process 100"
        assert not descriptor.HasField("thread")
        assert not descriptor.HasField("parent_uuid")
        assert not descriptor.HasField("counter")
        assert descriptor.HasField("process")
        assert descriptor.process.pid == 100
        assert descriptor.process.process_name == "Process 100"

    def test_process_descriptor_with_cmdline(self) -> None:
        data = build_track_descriptor(
            uuid=100,
            name="Process 100",
            pid=100,
            cmdline=["python", "-u", "script.py", "--arg1"],
            description="python -u script.py --arg1",
        )
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert descriptor.description == "python -u script.py --arg1"
        assert descriptor.HasField("process")
        assert descriptor.process.pid == 100
        assert descriptor.process.process_name == "Process 100"
        assert len(descriptor.process.cmdline) == 4
        assert descriptor.process.cmdline[0] == "python"
        assert descriptor.process.cmdline[1] == "-u"
        assert descriptor.process.cmdline[2] == "script.py"
        assert descriptor.process.cmdline[3] == "--arg1"

    def test_process_descriptor_no_cmdline_when_none(self) -> None:
        data = build_track_descriptor(uuid=100, name="Process 100", pid=100)
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert not descriptor.HasField("description")
        assert descriptor.HasField("process")
        assert len(descriptor.process.cmdline) == 0

    def test_process_descriptor_no_cmdline_when_empty(self) -> None:
        data = build_track_descriptor(uuid=100, name="Process 100", pid=100, cmdline=[])
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert not descriptor.HasField("description")
        assert descriptor.HasField("process")
        assert len(descriptor.process.cmdline) == 0

    def test_thread_descriptor(self) -> None:
        data = build_track_descriptor(
            uuid=200,
            name="Thread 0",
            pid=100,
            tid=0,
            parent_uuid=100,
            sibling_order_rank=0,
        )
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert descriptor.uuid == 200
        assert descriptor.name == "Thread 0"
        assert descriptor.parent_uuid == 100
        assert descriptor.sibling_order_rank == 0
        assert descriptor.HasField("thread")
        assert descriptor.thread.pid == 100
        assert descriptor.thread.tid == 0

    def test_counter_descriptor(self) -> None:
        data = build_track_descriptor(uuid=300, name="G0 collected", parent_uuid=200, is_counter=True)
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert descriptor.uuid == 300
        assert descriptor.name == "G0 collected"
        assert descriptor.parent_uuid == 200
        assert descriptor.counter.SerializeToString() == b""

    def test_counter_descriptor_with_share_key(self) -> None:
        data = build_track_descriptor(
            uuid=300,
            name="G0 collected",
            parent_uuid=200,
            is_counter=True,
            y_axis_share_key="collected",
        )
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert descriptor.uuid == 300
        assert descriptor.name == "G0 collected"
        assert descriptor.parent_uuid == 200
        assert descriptor.HasField("counter")
        assert descriptor.counter.y_axis_share_key == "collected"

    def test_process_descriptor_with_start_timestamp_ns(self) -> None:
        data = build_track_descriptor(
            uuid=100,
            name="Process 100",
            pid=100,
            start_timestamp_ns=1_700_000_000_123_456_789,
        )
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert descriptor.HasField("process")
        assert descriptor.process.start_timestamp_ns == 1_700_000_000_123_456_789

    def test_process_descriptor_without_start_timestamp_ns(self) -> None:
        """No start_timestamp_ns is written when the kwarg is omitted
        (default ``None``). The field must be absent from the bytes."""
        data = build_track_descriptor(uuid=100, name="Process 100", pid=100)
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert descriptor.HasField("process")
        assert not descriptor.process.HasField("start_timestamp_ns")

    def test_thread_descriptor_ignores_start_timestamp_ns(self) -> None:
        """``start_timestamp_ns`` is only valid on a process
        descriptor. A thread descriptor built with the kwarg must NOT
        emit it (the field is wrapped in a sub-message that we only
        emit for process descriptors)."""
        data = build_track_descriptor(
            uuid=200,
            name="Thread 0",
            pid=100,
            tid=0,
            parent_uuid=100,
            start_timestamp_ns=1_000,
        )
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert descriptor.HasField("thread")
        # ThreadDescriptor has no ``start_timestamp_ns`` field, so the
        # encoder must NOT write it in the thread submessage. Check by
        # verifying that the parsed + re-serialized thread submessage
        # matches the expected minimal payload.
        expected = ThreadDescriptor(pid=100, tid=0)
        assert descriptor.thread.SerializeToString() == expected.SerializeToString()


class TestBuildCounterDescriptor:
    """Wire-level tests for ``build_track_descriptor``'s
    ``y_axis_share_key`` kwarg and the resulting ``CounterDescriptor``
    submessage payload at ``TrackDescriptor.counter`` (field 8)."""

    def test_y_axis_share_key_emitted_at_field_8(self) -> None:
        data = build_track_descriptor(
            uuid=300,
            name="G0 collected",
            parent_uuid=200,
            is_counter=True,
            y_axis_share_key="collected",
        )
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert descriptor.HasField("counter")
        assert descriptor.counter.y_axis_share_key == "collected"

    def test_no_y_axis_share_key_emits_empty_submessage(self) -> None:
        data = build_track_descriptor(
            uuid=300,
            name="G0 collected",
            parent_uuid=200,
            is_counter=True,
        )
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert descriptor.counter.SerializeToString() == b""

    def test_y_axis_share_key_ignored_for_non_counter_track(self) -> None:
        data = build_track_descriptor(
            uuid=300,
            name="Track With Key",
            parent_uuid=200,
            is_counter=False,
            y_axis_share_key="ignored",
        )
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert not descriptor.HasField("counter")

    def test_only_share_key_field_is_set_no_other_counter_fields(self) -> None:
        data = build_track_descriptor(
            uuid=300,
            name="G0 duration",
            parent_uuid=200,
            is_counter=True,
            y_axis_share_key="duration",
        )
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert descriptor.HasField("counter")
        assert not descriptor.counter.HasField("type")
        assert len(descriptor.counter.categories) == 0
        assert not descriptor.counter.HasField("unit")
        assert not descriptor.counter.HasField("unit_multiplier")
        assert not descriptor.counter.HasField("is_incremental")
        assert not descriptor.counter.HasField("unit_name")
        assert descriptor.counter.y_axis_share_key == "duration"

    def test_y_axis_share_key_empty_string_treated_as_none(self) -> None:
        data = build_track_descriptor(
            uuid=300,
            name="G0 collected",
            parent_uuid=200,
            is_counter=True,
            y_axis_share_key="",
        )
        descriptor = TrackDescriptor()
        descriptor.ParseFromString(data)
        assert descriptor.counter.SerializeToString() == b""


class TestBuildTracePacket:
    def test_empty_packet(self) -> None:
        data = build_trace_packet(1)
        packet = TracePacket()
        packet.ParseFromString(data)
        assert packet.trusted_packet_sequence_id == 1

    def test_with_timestamp(self) -> None:
        data = build_trace_packet(1, timestamp=1_500_000_000)
        packet = TracePacket()
        packet.ParseFromString(data)
        assert packet.trusted_packet_sequence_id == 1
        assert packet.timestamp == 1_500_000_000

    def test_with_track_event(self) -> None:
        event = b"\x08\x01"
        data = build_trace_packet(1, track_event=event)
        packet = TracePacket()
        packet.ParseFromString(data)
        assert packet.trusted_packet_sequence_id == 1
        assert packet.track_event.SerializeToString() == event

    def test_with_track_descriptor(self) -> None:
        desc = b"\x0a\x05hello"
        data = build_trace_packet(1, track_descriptor=desc)
        packet = TracePacket()
        packet.ParseFromString(data)
        assert packet.trusted_packet_sequence_id == 1
        assert packet.track_descriptor.SerializeToString() == desc

    def test_with_all_fields(self) -> None:
        event = b"\x08\x01"
        data = build_trace_packet(42, timestamp=1000, track_event=event)
        packet = TracePacket()
        packet.ParseFromString(data)
        assert packet.trusted_packet_sequence_id == 42
        assert packet.timestamp == 1000
        assert packet.track_event.SerializeToString() == event


class TestBuildTrackEvent:
    def test_slice_begin(self) -> None:
        data = build_track_event(type=TrackEventType.SLICE_BEGIN, track_uuid=100, name="test")
        track_event = TrackEvent()
        track_event.ParseFromString(data)
        assert track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN
        assert track_event.track_uuid == 100
        assert track_event.name == "test"

    def test_slice_end(self) -> None:
        data = build_track_event(type=TrackEventType.SLICE_END, track_uuid=100)
        track_event = TrackEvent()
        track_event.ParseFromString(data)
        assert track_event.type == TrackEvent.Type.TYPE_SLICE_END
        assert track_event.track_uuid == 100
        assert not track_event.HasField("name")

    def test_instant(self) -> None:
        data = build_track_event(type=TrackEventType.INSTANT, track_uuid=100, name="marker")
        track_event = TrackEvent()
        track_event.ParseFromString(data)
        assert track_event.type == TrackEvent.Type.TYPE_INSTANT
        assert track_event.track_uuid == 100
        assert track_event.name == "marker"

    def test_counter(self) -> None:
        data = build_track_event(type=TrackEventType.COUNTER, track_uuid=100, counter_value=42)
        track_event = TrackEvent()
        track_event.ParseFromString(data)
        assert track_event.type == TrackEvent.Type.TYPE_COUNTER
        assert track_event.track_uuid == 100
        assert track_event.counter_value == 42

    def test_with_categories(self) -> None:
        data = build_track_event(
            type=TrackEventType.SLICE_BEGIN,
            track_uuid=100,
            name="test",
            categories=["cat1", "cat2"],
        )
        track_event = TrackEvent()
        track_event.ParseFromString(data)
        assert len(track_event.categories) == 2
        assert track_event.categories[0] == "cat1"
        assert track_event.categories[1] == "cat2"

    def test_with_debug_annotations(self) -> None:
        ann1 = b"\x52\x03key\x20\x2a"
        ann2 = b"\x52\x05other\x20\x64"
        data = build_track_event(
            type=TrackEventType.SLICE_BEGIN,
            track_uuid=100,
            name="test",
            debug_annotations=[ann1, ann2],
        )
        track_event = TrackEvent()
        track_event.ParseFromString(data)
        assert len(track_event.debug_annotations) == 2
        assert track_event.debug_annotations[0].name == "key"
        assert track_event.debug_annotations[0].int_value == 42
        assert track_event.debug_annotations[1].name == "other"
        assert track_event.debug_annotations[1].int_value == 100


class TestBuildTrace:
    def test_empty_trace(self) -> None:
        data = build_trace([])
        assert data == b""

    def test_single_packet(self) -> None:
        packet = b"\x40\x01"
        data = build_trace([packet])
        trace = Trace()
        trace.ParseFromString(data)
        assert len(trace.packet) == 1
        assert trace.packet[0].SerializeToString() == packet

    def test_multiple_packets(self) -> None:
        p1 = b"\x40\x01"
        p2 = b"\x40\x02"
        data = build_trace([p1, p2])
        trace = Trace()
        trace.ParseFromString(data)
        assert len(trace.packet) == 2
        assert trace.packet[0].SerializeToString() == p1
        assert trace.packet[1].SerializeToString() == p2


class TestConvertItemToPerfettoPackets:
    def test_cmdline_emitted_once_per_pid(self) -> None:
        state = PerfettoTrackState()
        state.set_cmdline(100, ["python", "script.py"])
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        desc1, _ = _convert_item(100, item, state, sequence_id=1)

        found_cmdline = False
        found_description = False
        for desc_bytes in desc1:
            packet = TracePacket()
            packet.ParseFromString(desc_bytes)
            if packet.HasField("track_descriptor"):
                td = packet.track_descriptor
                if td.description == "python script.py":
                    found_description = True
                if td.HasField("process") and len(td.process.cmdline) > 0:
                    assert len(td.process.cmdline) == 2
                    assert td.process.cmdline[0] == "python"
                    assert td.process.cmdline[1] == "script.py"
                    found_cmdline = True
        assert found_cmdline
        assert found_description, "description should be set when cmdline is present"

        desc2, _ = _convert_item(
            100,
            GCStatsInfo(
                gen=1,
                iid=0,
                ts_start=3_000,
                ts_stop=4_000,
                heap_size=2000,
                collections=2,
                collected=20,
                uncollectable=0,
                candidates=10,
                duration=0.002,
            ),
            state,
            sequence_id=1,
        )

        for desc_bytes in desc2:
            packet = TracePacket()
            packet.ParseFromString(desc_bytes)
            if packet.HasField("track_descriptor"):
                assert not packet.track_descriptor.HasField("process")

    def test_basic_item_emits_descriptors(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        descriptors, _ = _convert_item(100, item, state, sequence_id=1)
        assert len(descriptors) >= 2
        assert state.has_pid(100)
        assert state.has_tid(100, 0)

    def test_thread_track_has_sibling_order_rank_zero(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        descriptors, _ = _convert_item(100, item, state, sequence_id=1)
        proc_uuid = state.get_process_track_uuid(100)
        thread_uuid = state.get_thread_track_uuid(100, 0)
        thread_found = False
        for desc_bytes in descriptors:
            packet = TracePacket()
            packet.ParseFromString(desc_bytes)
            if packet.HasField("track_descriptor"):
                td = packet.track_descriptor
                if td.uuid == thread_uuid:
                    assert td.parent_uuid == proc_uuid
                    assert td.sibling_order_rank == 0
                    assert not td.HasField("child_ordering")
                    thread_found = True
        assert thread_found

    def test_counter_tracks_parented_to_counter_group(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        descriptors, _ = _convert_item(100, item, state, sequence_id=1)
        proc_uuid = state.get_process_track_uuid(100)
        group_uuid = state.get_or_create_counter_group_track_uuid(100, 0)
        assert group_uuid != proc_uuid
        group_seen = False
        per_metric_parent: dict[str, int] = {}
        for desc_bytes in descriptors:
            packet = TracePacket()
            packet.ParseFromString(desc_bytes)
            if packet.HasField("track_descriptor"):
                td = packet.track_descriptor
                uuid = td.uuid
                if td.HasField("counter"):
                    per_metric_parent[td.name] = td.parent_uuid
                elif uuid == group_uuid:
                    group_seen = True
                    assert td.parent_uuid == proc_uuid
                    assert td.child_ordering == 3
        assert group_seen, "GC Counters group track descriptor was not emitted"
        # heap_size is a top-level counter: parented directly to the process.
        assert per_metric_parent["heap_size"] == proc_uuid
        # Per-gen counters are parented to the GC Counters group.
        for name, parent_uuid in per_metric_parent.items():
            if name != "heap_size":
                assert parent_uuid == group_uuid, f"{name!r} should parent to group"

    def test_basic_item_emits_pause_slice(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        # Three packets are emitted before the GC pause slice: the
        # synthetic "Start Process" marker on the process track, then
        # the "Process 100" slice begin on the shared "Processes" track,
        # then the GC pause slice begin on the thread track. Find the
        # GC pause slice by name to disambiguate.
        assert len(packets) >= 3
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

        def _packet_name(p: bytes) -> str | None:
            packet = TracePacket()
            packet.ParseFromString(p)
            if not packet.HasField("track_event"):
                return None
            name = packet.track_event.name
            return name or None

        begin_packet = None
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if (
                packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN
                and packet.track_event.track_uuid != lifetime_uuid
                and packet.track_event.name == "GC Pause (gen=0)"
            ):
                begin_packet = p
                break
        assert begin_packet is not None
        first_packet = TracePacket()
        first_packet.ParseFromString(begin_packet)
        assert first_packet.timestamp == 1_000
        assert first_packet.HasField("track_event")
        assert first_packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN
        assert first_packet.track_event.name == "GC Pause (gen=0)"

    def test_basic_item_emits_counter_events(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=2,
            candidates=5,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        counter_packets: list[tuple[TracePacket, TrackEvent]] = []
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if packet.HasField("track_event") and packet.track_event.type == TrackEvent.Type.TYPE_COUNTER:
                counter_packets.append((packet, packet.track_event))
        assert len(counter_packets) == 5
        values = [track_event.counter_value for _, track_event in counter_packets]
        assert 10 in values
        assert 2 in values
        assert 5 in values
        assert 1000 in values
        # The `duration` value is encoded as a double (DOUBLE_COUNTER_VALUE,
        # field 44), not as a varint counter_value. Verify it is present.
        double_values = [track_event.double_counter_value for _, track_event in counter_packets]
        assert 0.001 in double_values

    def test_counter_descriptor_emitted_once(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        desc1, _ = _convert_item(100, item, state, sequence_id=1)
        desc2, _ = _convert_item(100, item, state, sequence_id=1)
        assert len(desc1) > 0
        assert len(desc2) == 0

    def test_invalid_timestamps_produces_events(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=2_000,
            ts_stop=1_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        descriptors, packets = _convert_item(100, item, state, sequence_id=1)
        assert len(descriptors) >= 2
        assert len(packets) >= 2

    def test_equal_timestamps_produces_events(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=1_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.0,
        )
        descriptors, packets = _convert_item(100, item, state, sequence_id=1)
        assert len(descriptors) >= 2
        assert len(packets) >= 2

    def test_incremental_item_emits_subphases(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=1,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=2048,
            collections=10,
            collected=100,
            uncollectable=1,
            candidates=20,
            duration=0.01,
            increment_size=500,
            alive_size=300,
            ts_mark_alive_start=3_000,
            ts_mark_alive_stop=3_100,
            ts_fill_increment_start=3_100,
            ts_fill_increment_stop=3_200,
            ts_deduce_unreachable_start=3_200,
            ts_deduce_unreachable_stop=3_300,
            ts_handle_weakref_callbacks_start=3_300,
            ts_handle_weakref_callbacks_stop=3_400,
            ts_finalize_garbage_stop=3_500,
            finalized_garbage_count=42,
            ts_handle_resurrected_stop=3_600,
            ts_clear_weakrefs_stop=3_700,
            clear_weakrefs_count=7,
            ts_delete_garbage_start=3_800,
            ts_delete_garbage_stop=3_900,
            deleted_garbage_count=13,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        slice_begins: list[str | None] = []
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if packet.HasField("track_event") and packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN:
                slice_begins.append(packet.track_event.name or None)
        assert "GC Pause (gen=1)" in slice_begins
        assert "Mark Alive (gen=1)" in slice_begins
        assert "Fill increment (gen=1)" in slice_begins
        assert "Deduce Unreachable (gen=1)" in slice_begins
        assert "Handle Weakrefs Callbacks (gen=1)" in slice_begins
        assert "Finalize Garbage (gen=1)" in slice_begins
        assert "Handle Resurrected (gen=1)" in slice_begins
        assert "Clear Weakrefs (gen=1)" in slice_begins
        assert "Delete Garbage (gen=1)" in slice_begins

    def test_uncollectable_counter_omitted_when_zero(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=5,
            collected=10,
            uncollectable=0,
            candidates=3,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        counter_uuids: set[int] = set()
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if not packet.HasField("track_event"):
                continue
            if packet.track_event.type != TrackEvent.Type.TYPE_COUNTER:
                continue
            counter_uuids.add(packet.track_event.track_uuid)
        # collected, candidates, heap_size, duration — no uncollectable counter.
        assert len(counter_uuids) == 4

    def test_uncollectable_counter_emitted_when_nonzero(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=5,
            collected=10,
            uncollectable=2,
            candidates=3,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        counter_uuids: set[int] = set()
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if not packet.HasField("track_event"):
                continue
            if packet.track_event.type != TrackEvent.Type.TYPE_COUNTER:
                continue
            counter_uuids.add(packet.track_event.track_uuid)
        # collected, uncollectable, candidates, heap_size, duration.
        assert len(counter_uuids) == 5

    def test_duration_counter_in_gc_metrics_group(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=5,
            collected=10,
            uncollectable=2,
            candidates=3,
            duration=0.42,
        )
        descriptors_packets, packets = _convert_item(100, item, state, sequence_id=1)
        # Find the per-gen `G0 duration` counter track UUID. The duration is
        # now split by generation (one `G{gen} duration` track per (pid, iid))
        # so a shared `duration` track is no longer emitted.
        duration_track_uuid: int | None = None
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if not packet.HasField("track_event"):
                continue
            track_event = packet.track_event
            if track_event.type == TrackEvent.Type.TYPE_COUNTER and track_event.double_counter_value == 0.42:
                duration_track_uuid = track_event.track_uuid
                break
        assert duration_track_uuid is not None

        # Find the matching TrackDescriptor and assert rank=4 (per-gen rank
        # for `duration` in the new layout) plus parent resolves to a track
        # named "GC Metrics".
        descriptors: dict[int, tuple[int, int, str]] = {}
        for p in descriptors_packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if not packet.HasField("track_descriptor"):
                continue
            td = packet.track_descriptor
            descriptors[td.uuid] = (
                td.parent_uuid,
                td.sibling_order_rank,
                td.name,
            )
        assert duration_track_uuid in descriptors
        parent, rank, _ = descriptors[duration_track_uuid]
        assert rank == 5
        assert parent != 0
        assert descriptors[parent][2] == "GC Metrics"

    def _make_full_incremental_item(self) -> GCStatsInfo:
        return GCStatsInfo(
            gen=1,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=2048,
            collections=10,
            collected=100,
            uncollectable=1,
            candidates=20,
            duration=0.01,
            increment_size=500,
            alive_size=300,
            ts_mark_alive_start=3_000,
            ts_mark_alive_stop=3_100,
            ts_fill_increment_start=3_100,
            ts_fill_increment_stop=3_200,
            ts_deduce_unreachable_start=3_200,
            ts_deduce_unreachable_stop=3_300,
            ts_handle_weakref_callbacks_start=3_300,
            ts_handle_weakref_callbacks_stop=3_400,
            ts_finalize_garbage_stop=3_500,
            finalized_garbage_count=42,
            ts_handle_resurrected_stop=3_600,
            ts_clear_weakrefs_stop=3_700,
            clear_weakrefs_count=7,
            ts_delete_garbage_start=3_800,
            ts_delete_garbage_stop=3_900,
            deleted_garbage_count=13,
        )

    def _annotations_for_slice(
        self,
        packets: list[bytes],
        slice_name: str,
    ) -> list[tuple[str | None, int | None]]:
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if not packet.HasField("track_event"):
                continue
            track_event = packet.track_event
            if track_event.type != TrackEvent.Type.TYPE_SLICE_BEGIN:
                continue
            if track_event.name != slice_name:
                continue
            out: list[tuple[str | None, int | None]] = []
            for ann in track_event.debug_annotations:
                out.append(
                    (
                        ann.name or None,
                        ann.int_value if ann.HasField("int_value") else None,
                    )
                )
            return out
        raise AssertionError(f"slice {slice_name!r} not found in packets")

    def test_finalize_garbage_substep_has_count_annotation(self) -> None:
        state = PerfettoTrackState()
        _, packets = _convert_item(100, self._make_full_incremental_item(), state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Finalize Garbage (gen=1)")
        assert ("finalized_garbage_count", 42) in anns
        assert all(name not in ("deleted_garbage_count", "clear_weakrefs_count") for name, _ in anns)

    def test_clear_weakrefs_substep_has_count_annotation(self) -> None:
        state = PerfettoTrackState()
        _, packets = _convert_item(100, self._make_full_incremental_item(), state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Clear Weakrefs (gen=1)")
        assert ("clear_weakrefs_count", 7) in anns
        assert all(name not in ("finalized_garbage_count", "deleted_garbage_count") for name, _ in anns)

    def test_delete_garbage_substep_has_count_annotation(self) -> None:
        state = PerfettoTrackState()
        _, packets = _convert_item(100, self._make_full_incremental_item(), state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Delete Garbage (gen=1)")
        assert ("deleted_garbage_count", 13) in anns
        assert all(name not in ("finalized_garbage_count", "clear_weakrefs_count") for name, _ in anns)

    def test_deduce_unreachable_substep_has_candidates_annotation(self) -> None:
        state = PerfettoTrackState()
        item = self._make_full_incremental_item()
        _, packets = _convert_item(100, item, state, sequence_id=1)
        anns = self._annotations_for_slice(packets, "Deduce Unreachable (gen=1)")
        assert ("candidates", item.candidates) in anns
        assert ("generation", 1) in anns

    def test_zero_duration_subphase_skipped(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=1,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=2048,
            collections=10,
            collected=100,
            uncollectable=1,
            candidates=20,
            duration=0.01,
            increment_size=500,
            alive_size=300,
            ts_mark_alive_start=3_000,
            ts_mark_alive_stop=3_000,
            ts_fill_increment_start=3_100,
            ts_fill_increment_stop=3_200,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        slice_names: list[str | None] = []
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if packet.HasField("track_event") and packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN:
                slice_names.append(packet.track_event.name or None)
        assert "Mark Alive (gen=1)" not in slice_names
        assert "Fill increment (gen=1)" in slice_names

    def test_multiple_threads(self) -> None:
        state = PerfettoTrackState()
        item0 = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        item1 = GCStatsInfo(
            gen=0,
            iid=1,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        desc0, _ = _convert_item(100, item0, state, sequence_id=1)
        desc1, _ = _convert_item(100, item1, state, sequence_id=1)
        assert len(desc0) >= 2
        assert len(desc1) >= 1
        assert state.has_tid(100, 0)
        assert state.has_tid(100, 1)

    def test_debug_annotation_name_wire_format(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=5,
            collected=10,
            uncollectable=2,
            candidates=3,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        # Three packets precede the GC pause slice begin: the synthetic
        # "Start Process" marker, the "Process 100" slice begin on the
        # shared "Processes" track, and any other warm-up events.
        # Identify the GC pause slice by its name.
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        begin_packet = None
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if (
                packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN
                and packet.track_event.track_uuid != lifetime_uuid
                and packet.track_event.name == "GC Pause (gen=0)"
            ):
                begin_packet = packet
                break
        first_packet = begin_packet
        assert first_packet is not None
        anns = first_packet.track_event.debug_annotations
        assert len(anns) == 7
        for ann in anns:
            assert not ann.HasField("name_iid"), (
                "field 1 of DebugAnnotation is `name_iid` (uint64); the annotation name must not be written there"
            )
            assert ann.HasField("name")

    def test_debug_annotations_on_pause(self) -> None:
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=5,
            collected=10,
            uncollectable=2,
            candidates=3,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        # Disambiguate by name (and exclude the spec-15 "Processes" track
        # slice begin) to find the GC pause slice.
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        begin_packet = None
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if (
                packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN
                and packet.track_event.track_uuid != lifetime_uuid
                and packet.track_event.name == "GC Pause (gen=0)"
            ):
                begin_packet = p
                break
        assert begin_packet is not None
        first_packet = TracePacket()
        first_packet.ParseFromString(begin_packet)
        anns = first_packet.track_event.debug_annotations
        assert len(anns) == 7
        ann_values: list[tuple[str | None, int | None]] = []
        for ann in anns:
            name = ann.name or None
            val = ann.int_value if ann.HasField("int_value") else None
            ann_values.append((name, val))
        assert ("generation", 0) in ann_values
        assert ("iid", 0) in ann_values
        assert ("collections", 5) in ann_values
        assert ("heap_size", 1000) in ann_values
        assert ("collected", 10) in ann_values
        assert ("uncollectable", 2) in ann_values
        assert ("candidates", 3) in ann_values

    def test_process_lifetime_track_emitted_once(self) -> None:
        """The ``Processes`` track descriptor is emitted at most
        once for a single pid, even across multiple convert passes."""
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        descriptors, convert_packets, closeout = _convert_items(
            [
                (100, item),
                (
                    100,
                    GCStatsInfo(
                        gen=1,
                        iid=0,
                        ts_start=3_000,
                        ts_stop=4_000,
                        heap_size=2000,
                        collections=2,
                        collected=20,
                        uncollectable=0,
                        candidates=10,
                        duration=0.002,
                    ),
                ),
            ],
            state,
            sequence_id=1,
        )
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        # The convert passes emit no "Processes" descriptor at all; the
        # track is described once, at closeout, alongside its slices.
        seen = 0
        for desc_bytes in (*descriptors, *convert_packets, *closeout):
            packet = TracePacket()
            packet.ParseFromString(desc_bytes)
            if packet.HasField("track_descriptor"):
                td = packet.track_descriptor
                if td.uuid == lifetime_uuid:
                    assert td.name == _PROCESS_LIFETIME_TRACK_NAME
                    # The descriptor carries no parent_uuid (root), no
                    # process, no thread, no counter, no child_ordering,
                    # no sibling_order_rank, no description.
                    assert not td.HasField("parent_uuid")
                    assert not td.HasField("process")
                    assert not td.HasField("thread")
                    assert not td.HasField("counter")
                    assert not td.HasField("child_ordering")
                    assert not td.HasField("sibling_order_rank")
                    assert not td.HasField("description")
                    seen += 1
        assert seen == 1, f"expected exactly one Processes track descriptor, got {seen}"
        assert not any(TracePacket.FromString(d).track_descriptor.uuid == lifetime_uuid for d in descriptors), (
            "the Processes descriptor must come from finalize, not from a convert pass"
        )

    def test_process_lifetime_slice_begin_at_first_event_ts(self) -> None:
        """The ``Process <pid>`` slice BEGIN is emitted at the ts of the
        first non-meta event for the pid, on the shared ``Processes``
        track. It carries the observed span as ``real_start_ts`` /
        ``real_end_ts``, plus a ``cmdline`` debug annotation joined with
        single spaces when ``state`` has a cmdline recorded for the pid."""
        state = PerfettoTrackState()
        state.set_cmdline(100, ["python3", "-m", "fake_target"])
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        begin_packets: list[TracePacket] = []
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if (
                packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN
                and packet.track_event.track_uuid == lifetime_uuid
            ):
                begin_packets.append(packet)
        assert len(begin_packets) == 1, f"expected exactly one slice BEGIN on Processes track, got {len(begin_packets)}"
        first_packet = begin_packets[0]
        assert first_packet.timestamp == 1_000
        assert first_packet.track_event.name == "Process 100"
        annotations = first_packet.track_event.debug_annotations
        assert [a.name for a in annotations] == ["cmdline", "real_start_ts", "real_end_ts"]
        assert annotations[0].string_value == "python3 -m fake_target"
        assert annotations[1].int_value == 1_000
        assert annotations[2].int_value == 2_000

    def test_process_lifetime_slice_begin_no_cmdline_omits_arg(self) -> None:
        """When ``state`` has no cmdline for the pid, the slice BEGIN on
        the ``Processes`` track carries only the observed span: the
        ``cmdline`` annotation is dropped, the ``real_*`` pair is not,
        since every slice records its span whatever else is known."""
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        begin_packets: list[TracePacket] = []
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if (
                packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN
                and packet.track_event.track_uuid == lifetime_uuid
            ):
                begin_packets.append(packet)
        assert len(begin_packets) == 1
        annotations = begin_packets[0].track_event.debug_annotations
        assert [a.name for a in annotations] == ["real_start_ts", "real_end_ts"]

    def test_process_lifetime_slice_end_at_last_event_ts(self) -> None:
        """The ``Process <pid>`` slice END is emitted at the ts of the
        last non-meta event for the pid, on the shared ``Processes``
        track."""
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        _, packets = _convert_item(100, item, state, sequence_id=1)
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        end_packets: list[TracePacket] = []
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if (
                packet.track_event.type == TrackEvent.Type.TYPE_SLICE_END
                and packet.track_event.track_uuid == lifetime_uuid
            ):
                end_packets.append(packet)
        assert len(end_packets) == 1, f"expected exactly one slice END on Processes track, got {len(end_packets)}"
        # Last non-meta event ts in this fixture is 2_000 (ts_stop).
        assert end_packets[0].timestamp == 2_000
        assert end_packets[0].track_event.name == "Process 100"

    def test_process_lifetime_two_pids_one_shared_track(self) -> None:
        """Two distinct pids share the same ``Processes`` track UUID and
        each get their own slice pair. These two spans cross -- pid 100
        runs ``[500, 1500]`` and pid 200 ``[1000, 5000]`` -- so pid 100's
        end is clipped to one nanosecond before pid 200 begins. Both
        BEGINs record their observed span in ``real_start_ts`` /
        ``real_end_ts``, so pid 100's clipped end is recoverable and pid
        200's untouched one reads the same way. Each BEGIN also carries
        a ``cmdline`` annotation reflecting that pid's recorded
        cmdline."""
        state = PerfettoTrackState()
        state.set_cmdline(100, ["python3", "-m", "early_target"])
        state.set_cmdline(200, ["python3", "-m", "late_target"])
        item_late_pid = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=5_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        item_early_pid = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=500,
            ts_stop=1_500,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        _, convert_packets, closeout = _convert_items(
            [(200, item_late_pid), (100, item_early_pid)],
            state,
            sequence_id=1,
        )
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

        assert _lifetime_slices(convert_packets, lifetime_uuid) == [], (
            "convert passes must emit no Processes-track slices"
        )
        # Emitted in stack order: pid 100 opens first, is closed at 999
        # because pid 200 crosses it, then pid 200 opens and closes.
        assert _lifetime_slices(closeout, lifetime_uuid) == [
            (
                500,
                TrackEventType.SLICE_BEGIN,
                "Process 100",
                {
                    "cmdline": "python3 -m early_target",
                    "real_start_ts": 500,
                    "real_end_ts": 1_500,
                },
            ),
            (999, TrackEventType.SLICE_END, "Process 100", {}),
            (
                1_000,
                TrackEventType.SLICE_BEGIN,
                "Process 200",
                {
                    "cmdline": "python3 -m late_target",
                    "real_start_ts": 1_000,
                    "real_end_ts": 5_000,
                },
            ),
            (5_000, TrackEventType.SLICE_END, "Process 200", {}),
        ]

    def test_process_lifetime_idempotent_across_converts(self) -> None:
        """Two convert passes for the same pid produce a single slice
        pair spanning both batches: the second pass widens the recorded
        span, and the pair is emitted once at closeout. One pid alone can
        never cross anything, so the drawn span and the ``real_*``
        annotations agree."""
        state = PerfettoTrackState()
        state.set_cmdline(100, ["python3", "-m", "fake_target"])
        item1 = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        item2 = GCStatsInfo(
            gen=1,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=2000,
            collections=2,
            collected=20,
            uncollectable=0,
            candidates=10,
            duration=0.002,
        )
        _, convert_packets, closeout = _convert_items(
            [(100, item1), (100, item2)],
            state,
            sequence_id=1,
        )
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

        assert _lifetime_slices(convert_packets, lifetime_uuid) == []
        # One pair only, spanning the first batch's ts_start to the
        # second batch's ts_stop.
        assert _lifetime_slices(closeout, lifetime_uuid) == [
            (
                1_000,
                TrackEventType.SLICE_BEGIN,
                "Process 100",
                {
                    "cmdline": "python3 -m fake_target",
                    "real_start_ts": 1_000,
                    "real_end_ts": 4_000,
                },
            ),
            (4_000, TrackEventType.SLICE_END, "Process 100", {}),
        ]


class TestConvertInstantToPerfettoPacket:
    def test_emits_process_descriptor(self) -> None:
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            instant_event(100, "start", ts_ns=5_000),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        # 1 root descriptor + 1 process descriptor. The "Processes" track
        # descriptor is emitted at closeout, not here.
        assert len(descriptors) == 2
        assert state.has_pid(100)

    def test_emits_instant_event(self) -> None:
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            instant_event(100, "start GC monitor", ts_ns=5_000),
        ]
        _, packets = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        # Two packets from the convert call: the synthetic "Start
        # Process" marker (process track) and the user-provided instant
        # event (process track). This pid's whole observed span is a
        # single ts, so its "Processes" slice is zero-length -- and it is
        # still drawn, so finalize adds the track descriptor plus a
        # BEGIN/END pair, both at ts 5_000.
        packets.extend(finalize_perfetto_packets(state, sequence_id=1))
        assert len(packets) == 5
        names: list[str | None] = []
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if packet.HasField("track_event"):
                names.append(packet.track_event.name or None)
        assert names == [
            _START_PROCESS_MARKER_NAME,
            "start GC monitor",
            "Process 100",
            "Process 100",
        ]
        instant_packet = None
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if packet.track_event.name == "start GC monitor":
                instant_packet = packet
                break
        assert instant_packet is not None
        assert instant_packet.timestamp == 5_000
        assert instant_packet.track_event.type == TrackEvent.Type.TYPE_INSTANT
        assert instant_packet.track_event.name == "start GC monitor"

    def test_reuses_process_descriptor(self) -> None:
        state = PerfettoTrackState()
        desc1, packets1 = convert_trace_events_to_perfetto(
            [process_meta(100, "Process 100"), instant_event(100, "start", ts_ns=5_000)],
            state,
            sequence_id=1,
        )
        desc2, packets2 = convert_trace_events_to_perfetto(
            [process_meta(100, "Process 100"), instant_event(100, "stop", ts_ns=10_000)],
            state,
            sequence_id=1,
        )
        # First call: 2 descriptors (root + process) + 2 packets from the
        # convert (marker + instant). Second call: 0 descriptors (all are
        # idempotent) + 1 packet (the new instant event). The whole
        # "Processes" pair -- descriptor, BEGIN, END -- comes from the
        # single finalize, spanning both calls' timestamps.
        assert len(desc1) == 2
        assert len(packets1) == 2
        assert len(desc2) == 0
        assert len(packets2) == 1
        closeout = finalize_perfetto_packets(state, sequence_id=1)
        assert len(closeout) == 3
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        assert _lifetime_slices(closeout, lifetime_uuid) == [
            (
                5_000,
                TrackEventType.SLICE_BEGIN,
                "Process 100",
                {"real_start_ts": 5_000, "real_end_ts": 10_000},
            ),
            (10_000, TrackEventType.SLICE_END, "Process 100", {}),
        ]

    def test_instant_after_gc_event_no_duplicate_descriptor(self) -> None:
        state = PerfettoTrackState()
        gc_item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        gc_desc, _ = _convert_item(100, gc_item, state, sequence_id=1)
        inst_desc, _ = convert_trace_events_to_perfetto(
            [process_meta(100, "Process 100"), instant_event(100, "stop", ts_ns=5_000)],
            state,
            sequence_id=1,
        )
        assert len(gc_desc) >= 2
        assert len(inst_desc) == 0

    def test_single_arg_counter_uses_metric_name_as_track_name(self) -> None:
        state = PerfettoTrackState()
        descriptors, _ = convert_trace_events_to_perfetto(
            [
                process_meta(100, "Process 100"),
                thread_meta(100, 0, "Thread 0"),
                counter_event(pid=100, tid=0, name="heap_size", ts_ns=1_000, args={"heap_size": 1234}),
            ],
            state,
            sequence_id=1,
        )
        track_names: list[str] = []
        for d in descriptors:
            packet = TracePacket()
            packet.ParseFromString(d)
            if packet.HasField("track_descriptor"):
                td = packet.track_descriptor
                if td.HasField("counter") and td.name:
                    track_names.append(td.name)
        assert "heap_size" in track_names
        assert "heap_size heap_size" not in track_names

    def test_shared_heap_size_track_reused_across_generations(self) -> None:
        state = PerfettoTrackState()
        item_g0 = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        item_g1 = GCStatsInfo(
            gen=1,
            iid=0,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=2000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        _convert_item(100, item_g0, state, sequence_id=1)
        uuid_after_g0 = state.get_or_create_counter_track_uuid(100, 0, "heap_size", "heap_size")
        _convert_item(100, item_g1, state, sequence_id=1)
        uuid_after_g1 = state.get_or_create_counter_track_uuid(100, 0, "heap_size", "heap_size")
        assert uuid_after_g0 == uuid_after_g1

    def test_no_closeout_emitted_during_convert(self) -> None:
        """``convert_trace_events_to_perfetto`` never emits a
        ``TYPE_SLICE_END`` on the ``Processes`` track; closeout is the
        caller's job (see ``finalize_perfetto_packets``)."""
        state = PerfettoTrackState()
        item = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        gc_events = convert_item_to_trace_format(100, item)
        meta: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            thread_meta(100, item.iid, f"Thread {item.iid}"),
        ]
        _, packets = convert_trace_events_to_perfetto(
            meta + gc_events,
            state,
            sequence_id=1,
        )
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        end_packets: list[TracePacket] = []
        for p in packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if (
                packet.track_event.type == TrackEvent.Type.TYPE_SLICE_END
                and packet.track_event.track_uuid == lifetime_uuid
            ):
                end_packets.append(packet)
        assert end_packets == [], (
            f"convert_trace_events_to_perfetto must not emit slice END "
            f"on the Processes track; got {len(end_packets)} ENDs"
        )
        # Calling finalize_perfetto_packets now produces exactly one END.
        closeout = finalize_perfetto_packets(state, sequence_id=1)
        end_packets = []
        for p in closeout:
            packet = TracePacket()
            packet.ParseFromString(p)
            if (
                packet.track_event.type == TrackEvent.Type.TYPE_SLICE_END
                and packet.track_event.track_uuid == lifetime_uuid
            ):
                end_packets.append(packet)
        assert len(end_packets) == 1
        assert end_packets[0].timestamp == 2_000

    def test_closeout_emitted_only_at_finalize(self) -> None:
        """Across two ``convert_trace_events_to_perfetto`` calls for the
        same pid, the convert call never emits a slice END on the
        ``Processes`` track (the END is the caller's job, and
        ``finalize_perfetto_packets`` is called exactly once at the end
        of the trace). The single END's ts is the last non-counter
        non-meta event ts of the *second* convert call, not the first.
        """
        state = PerfettoTrackState()
        item1 = GCStatsInfo(
            gen=0,
            iid=0,
            ts_start=1_000,
            ts_stop=2_000,
            heap_size=1000,
            collections=1,
            collected=10,
            uncollectable=0,
            candidates=5,
            duration=0.001,
        )
        item2 = GCStatsInfo(
            gen=1,
            iid=1,
            ts_start=3_000,
            ts_stop=4_000,
            heap_size=2000,
            collections=2,
            collected=20,
            uncollectable=0,
            candidates=10,
            duration=0.002,
        )
        events1: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            thread_meta(100, item1.iid, f"Thread {item1.iid}"),
            *convert_item_to_trace_format(100, item1),
        ]
        events2: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            thread_meta(100, item2.iid, f"Thread {item2.iid}"),
            *convert_item_to_trace_format(100, item2),
        ]
        _, packets1 = convert_trace_events_to_perfetto(
            events1,
            state,
            sequence_id=1,
        )
        _, packets2 = convert_trace_events_to_perfetto(
            events2,
            state,
            sequence_id=1,
        )
        # finalize is called exactly once at the end (mimicking
        # encoder.close()).
        closeout = finalize_perfetto_packets(state, sequence_id=1)
        all_packets = packets1 + packets2 + closeout
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

        def _count(packets: list[bytes], event_type: int) -> int:
            n = 0
            for p in packets:
                packet = TracePacket()
                packet.ParseFromString(p)
                if packet.track_event.track_uuid == lifetime_uuid and packet.track_event.type == event_type:
                    n += 1
            return n

        # Neither batch emits anything on the Processes track; both ends
        # of the pair are the finalize pass's job.
        assert _count(packets1, TrackEvent.Type.TYPE_SLICE_BEGIN) == 0
        assert _count(packets1, TrackEvent.Type.TYPE_SLICE_END) == 0
        assert _count(packets2, TrackEvent.Type.TYPE_SLICE_BEGIN) == 0
        assert _count(packets2, TrackEvent.Type.TYPE_SLICE_END) == 0
        # The finalize pass: exactly one pair.
        assert _count(closeout, TrackEvent.Type.TYPE_SLICE_BEGIN) == 1
        assert _count(closeout, TrackEvent.Type.TYPE_SLICE_END) == 1
        # Across the union, exactly one BEGIN and one END.
        assert _count(all_packets, TrackEvent.Type.TYPE_SLICE_BEGIN) == 1
        assert _count(all_packets, TrackEvent.Type.TYPE_SLICE_END) == 1
        # The single END's ts is the last non-counter non-meta event ts
        # of the *second* batch (4_000), not the first (2_000).
        end_packet = None
        for p in all_packets:
            packet = TracePacket()
            packet.ParseFromString(p)
            if (
                packet.track_event.type == TrackEvent.Type.TYPE_SLICE_END
                and packet.track_event.track_uuid == lifetime_uuid
            ):
                end_packet = packet
                break
        assert end_packet is not None
        assert end_packet.timestamp == 4_000
        # Calling finalize again is a no-op (state is drained).
        assert finalize_perfetto_packets(state, sequence_id=1) == []


def _track_descriptor(packet_bytes: bytes) -> TrackDescriptor | None:
    """Extract the inner ``TrackDescriptor`` from a ``TracePacket``.

    Returns ``None`` if the packet is not a track-descriptor packet.
    """
    packet = TracePacket()
    packet.ParseFromString(packet_bytes)
    return packet.track_descriptor if packet.HasField("track_descriptor") else None


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
        td = _track_descriptor(d)
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
        td = _track_descriptor(d)
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
            process_meta(100, "Process 100"),
            instant_event(100, "start", ts_ns=5_000),
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
            process_meta(100, "Process 100"),
            instant_event(100, "first", ts_ns=1_000),
        ]
        events2: list[TraceEvent] = [
            process_meta(200, "Process 200"),
            instant_event(200, "second", ts_ns=2_000),
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
            process_meta(1, "Process 1"),
            instant_event(1, "ev1", ts_ns=2_000),
            process_meta(2, "Process 2"),
            instant_event(2, "ev2", ts_ns=1_000),
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
            process_meta(2, "Process 2"),
            instant_event(2, "ev", ts_ns=1_000),
            process_meta(1, "Process 1"),
            instant_event(1, "ev", ts_ns=1_000),
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

    def test_meta_only_pid_has_no_sibling_order_rank(self) -> None:
        """A pid with only ProcessMeta / ThreadMeta (no non-meta events)
        must not carry a ``sibling_order_rank`` on its descriptor."""
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        tds = _process_descriptor_fields_for_pid(descriptors, 100)
        assert len(tds) == 1
        assert not tds[0].HasField("sibling_order_rank")

    def test_meta_events_do_not_contribute_to_first_ts(self) -> None:
        """``ProcessMeta`` / ``ThreadMeta`` must not set the first
        event ts; the rank is driven solely by non-meta events."""
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
            process_meta(200, "Process 200"),
            thread_meta(200, 0, "Thread 0"),
            instant_event(100, "late", ts_ns=5_000),
            instant_event(200, "early", ts_ns=1_000),
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
            process_meta(1, "Process 1"),
            process_meta(2, "Process 2"),
            instant_event(2, "ev", ts_ns=2_000),
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
            return [
                ev
                for pid in ordered_pids
                for ev in (
                    process_meta(pid, f"Process {pid}"),
                    instant_event(pid, "ev", ts_ns=ts_map[pid]),
                )
            ]

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
            [process_meta(1, "p1"), instant_event(1, "a", ts_ns=1_000)],
            s,
            sequence_id=1,
        )
        d2, _ = convert_trace_events_to_perfetto(
            [process_meta(2, "p2"), instant_event(2, "b", ts_ns=5_000)],
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
            process_meta(100, "Process 100"),
            instant_event(100, "start", ts_ns=5_000),
            process_meta(200, "Process 200"),
            instant_event(200, "start", ts_ns=1_000),
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

    def test_meta_only_pid_has_no_start_timestamp_ns(self) -> None:
        """A pid with only ``ProcessMeta`` / ``ThreadMeta`` (no
        non-meta events) has no recorded first-ts, so
        ``start_timestamp_ns`` must be absent from the descriptor."""
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        tds = _process_descriptor_fields_for_pid(descriptors, 100)
        assert len(tds) == 1
        assert not tds[0].process.HasField("start_timestamp_ns")

    def test_start_timestamp_ns_uses_ts_start_for_gc_stats(self) -> None:
        """For ``TGCStatsInfo`` events, the first-ts (and therefore
        ``start_timestamp_ns``) is the ``ts_start`` of the first GC
        pause, not the ``ts_stop`` or any sub-event ts."""
        from gcmon.data import GCStatsInfo

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
            process_meta(1, "Process 1"),
            process_meta(2, "Process 2"),
            instant_event(2, "ev", ts_ns=2_000),
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
            [process_meta(1, "p1"), instant_event(1, "a", ts_ns=1_000)],
            s,
            sequence_id=1,
        )
        d2, _ = convert_trace_events_to_perfetto(
            [process_meta(2, "p2"), instant_event(2, "b", ts_ns=5_000)],
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


def _counter_track_y_axis_share_key(
    descriptors: list[bytes],
    track_name: str,
) -> str | None:
    """Find the counter TrackDescriptor whose name equals *track_name*
    and return its ``y_axis_share_key`` (or ``None`` if the
    ``CounterDescriptor`` submessage is empty). Returns ``None`` if no
    such track descriptor exists at all.
    """
    for d in descriptors:
        td = _track_descriptor(d)
        if td is None:
            continue
        if td.name != track_name:
            continue
        if not td.HasField("counter") or td.counter.SerializeToString() == b"":
            return None
        return td.counter.y_axis_share_key or None
    return None


class TestCounterTrackYAxisShareKey:
    """End-to-end wire tests that drive ``convert_trace_events_to_perfetto``
    and inspect the resulting counter track descriptors for the
    ``y_axis_share_key`` value."""

    def test_grouped_counters_share_y_axis_by_metric(self) -> None:
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
            counter_event(100, 0, "G0", 1_000, {"collected": 100, "candidates": 50, "duration": 0.005}),
            counter_event(100, 0, "G1", 1_001, {"collected": 80, "candidates": 40, "duration": 0.004}),
            counter_event(100, 0, "G2", 1_002, {"collected": 60, "candidates": 30, "duration": 0.003}),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        for gen in ("G0", "G1", "G2"):
            for metric in ("collected", "candidates", "duration"):
                track_name = f"{gen} {metric}"
                assert _counter_track_y_axis_share_key(descriptors, track_name) == metric, (
                    f"{track_name} should share Y-axis under {metric!r}"
                )

    def test_heap_size_has_no_share_key(self) -> None:
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
            counter_event(100, 0, "heap_size", 1_000, {"heap_size": 4096}),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        assert _counter_track_y_axis_share_key(descriptors, "heap_size") is None

    def test_uncollectable_share_key_emitted_when_nonzero(self) -> None:
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
            counter_event(
                100,
                0,
                "G0",
                1_000,
                {"collected": 1, "uncollectable": 1, "candidates": 1, "duration": 1},
            ),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        assert _counter_track_y_axis_share_key(descriptors, "G0 uncollectable") == "uncollectable"

    def test_different_pids_have_independent_share_groups(self) -> None:
        """Two pids each emit a ``G0 collected`` counter. Both must
        carry ``y_axis_share_key = "collected"``; the parent-scoping
        is what the docs require for safe sharing, and is implicit in
        the existing per-``(pid, tid)`` ``GC Metrics`` group.

        Multiple metric args are used so the track name resolves to
        ``"G0 collected"`` (the encoder names a single-arg counter
        track by the metric itself, e.g. ``"collected"``).
        """
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
            counter_event(
                100,
                0,
                "G0",
                1_000,
                {"collected": 10, "candidates": 5},
            ),
            process_meta(200, "Process 200"),
            thread_meta(200, 0, "Thread 0"),
            counter_event(
                200,
                0,
                "G0",
                1_001,
                {"collected": 20, "candidates": 6},
            ),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(
            events,
            state,
            sequence_id=1,
        )
        parent_uuids: set[int] = set()
        for d in descriptors:
            td = _track_descriptor(d)
            if td is None:
                continue
            if td.name != "G0 collected":
                continue
            parent = td.parent_uuid
            assert parent != 0
            parent_uuids.add(parent)
            assert td.HasField("counter") and td.counter.SerializeToString() != b""
            assert td.counter.y_axis_share_key == "collected"
        assert len(parent_uuids) == 2, (
            f"expected G0 collected tracks under 2 distinct parent groups "
            f"(one per pid), got {len(parent_uuids)}: {parent_uuids}"
        )


class TestRssCounterTrack:
    """RSS counter track shape and process-level parenting."""

    def test_counter_track_parented_to_process(self) -> None:
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            counter_event(100, -1, "rss", 1_000, {"rss": 4096}),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        proc_uuid = state.get_process_track_uuid(100)
        ctr_key = (100, -1, "rss", "rss")
        assert state.has_counter_track(*ctr_key)
        ctr_uuid = state.get_or_create_counter_track_uuid(*ctr_key)
        found_ctr = False
        for d in descriptors:
            td = _track_descriptor(d)
            if td is None:
                continue
            if td.uuid == ctr_uuid:
                assert td.parent_uuid == proc_uuid, (
                    f"RSS counter track parent should be process track; "
                    f"got parent_uuid={td.parent_uuid}, expected {proc_uuid}"
                )
                assert td.name == "rss"
                assert td.HasField("counter")
                found_ctr = True
                break
        assert found_ctr, "RSS counter track descriptor was not emitted"

    def test_display_name_is_metric_name(self) -> None:
        """With a single-arg counter, ``display_name`` defaults to the
        metric name ``"rss"`` (the single arg name)."""
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            counter_event(100, -1, "rss", 1_000, {"rss": 8192}),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        ctr_key = (100, -1, "rss", "rss")
        ctr_uuid = state.get_or_create_counter_track_uuid(*ctr_key)
        for d in descriptors:
            td = _track_descriptor(d)
            if td is not None and td.uuid == ctr_uuid:
                assert td.name == "rss"
                return
        pytest.fail("RSS counter track descriptor not found")

    def test_no_thread_descriptor_for_rss_tid(self) -> None:
        """No ``ThreadDescriptor`` track should be emitted for
        ``tid=-1`` — RSS is process-level."""
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            counter_event(100, -1, "rss", 1_000, {"rss": 4096}),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        for d in descriptors:
            td = _track_descriptor(d)
            if td is not None and td.HasField("thread"):
                pytest.fail(f"unexpected thread descriptor for RSS: uuid={td.uuid}")

    def test_multiple_pids_get_separate_rss_tracks(self) -> None:
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            counter_event(100, -1, "rss", 1_000, {"rss": 4096}),
            process_meta(200, "Process 200"),
            counter_event(200, -1, "rss", 2_000, {"rss": 8192}),
        ]
        _, _ = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        for pid in (100, 200):
            ctr_key = (pid, -1, "rss", "rss")
            assert state.has_counter_track(*ctr_key), f"no RSS track for pid {pid}"
        # Each RSS counter track is parented to the respective process
        # track, and process tracks have distinct UUIDs.
        assert state.get_process_track_uuid(100) != state.get_process_track_uuid(200)

    def test_rss_renders_at_top_level(self) -> None:
        """RSS is a top-level counter metric, parented directly to the
        process track — NOT inside the GC Metrics group."""
        state = PerfettoTrackState()
        events: list[TraceEvent] = [
            process_meta(100, "Process 100"),
            thread_meta(100, 0, "Thread 0"),
            # RSS sample (tid=-1, process-level)
            counter_event(100, -1, "rss", 1_000, {"rss": 4096}),
            # GC counter (tid=0, thread-level, inside GC Metrics group)
            counter_event(100, 0, "G0", 1_000, {"collected": 42, "candidates": 10, "duration": 0.005}),
        ]
        descriptors, _ = convert_trace_events_to_perfetto(events, state, sequence_id=1)
        proc_uuid = state.get_process_track_uuid(100)
        rss_key = (100, -1, "rss", "rss")
        rss_uuid = state.get_or_create_counter_track_uuid(*rss_key)
        g0_uuid = state.get_or_create_counter_track_uuid(100, 0, "G0", "duration")
        rss_parent = None
        g0_parent = None
        for d in descriptors:
            td = _track_descriptor(d)
            if td is None:
                continue
            if td.uuid == rss_uuid:
                rss_parent = td.parent_uuid
            elif td.uuid == g0_uuid:
                g0_parent = td.parent_uuid
        assert rss_parent == proc_uuid, "RSS should be parented directly to process track"
        assert g0_parent is not None and g0_parent != proc_uuid, (
            "GC counters should be inside GC Metrics group, not directly on process track"
        )

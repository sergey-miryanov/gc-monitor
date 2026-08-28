"""Tests for the shared ``Processes`` track: spans, clipping, closeout.

The clipping sweep is covered twice over: directly as a pure function,
and through ``finalize_perfetto_packets``, where the emitted BEGIN/END
pairs also have to be ones the trace processor can pair up. See ADR-0011.
"""

import random

import pytest
from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import (
    TracePacket,
    TrackEvent,
)

from gcmon.exporters.perfetto_format import convert_trace_events_to_perfetto
from gcmon.exporters.perfetto_process_lifetime import (
    _clip_spans_to_laminar,
    _emit_process_lifetime_track_descriptor,
    finalize_perfetto_packets,
)
from gcmon.exporters.perfetto_proto import TrackEventType
from gcmon.exporters.perfetto_track_state import PerfettoTrackState
from gcmon.exporters.trace_converter import convert_item_to_trace_format
from gcmon.model.data import GCStatsInfo
from gcmon.model.trace_event import TraceEvent
from tests.exporters.perfetto_helpers import (
    convert_item,
    convert_items,
    lifetime_slices,
)

# Name of the shared top-level Perfetto track that holds one slice per
# pid spanning the first-to-last non-meta event timestamps for that
# pid. Must match ``_PROCESS_LIFETIME_TRACK_NAME`` in
# ``gcmon.exporters.perfetto_process_lifetime``.
_PROCESS_LIFETIME_TRACK_NAME: str = "Processes"


def _finalize_spans(
    spans: list[tuple[int, int, int]],
) -> tuple[dict[int, tuple[int, int]], dict[int, tuple[int, int]]]:
    """Run *spans* -- ``[(pid, start, end), ...]`` -- through
    ``finalize_perfetto_packets`` and decode the result.

    Returns ``({pid: (ts, end_ts)}, {pid: (real_start_ts, real_end_ts)})``:
    the span each slice *draws*, and the span it *records*. Every pid
    with a span appears in both -- nothing is ever dropped -- so the two
    dicts always have the same keys.

    Also asserts the emitted order is one the trace processor can pair
    up: each BEGIN is immediately followed by its own END, and of two
    BEGINs on one timestamp the first outlives the second. Getting the
    second wrong corrupts both slices, since a named END force-closes
    whatever sits above the slice it matched.
    """
    state = PerfettoTrackState()
    for pid, start, end in spans:
        state.update_process_lifetime(pid, start)
        state.update_process_lifetime(pid, end)
    packets = finalize_perfetto_packets(state, sequence_id=1)
    lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

    emitted = lifetime_slices(packets, lifetime_uuid)
    intervals: dict[int, tuple[int, int]] = {}
    real: dict[int, tuple[int, int]] = {}
    open_slice: tuple[str, int] | None = None
    for ts, event_type, name, annotations in emitted:
        if event_type == TrackEventType.SLICE_BEGIN:
            assert open_slice is None, f"BEGIN for {name!r} while {open_slice} is still open"
            open_slice = (name, ts)
            real[int(name.removeprefix("Process "))] = (
                int(annotations["real_start_ts"]),
                int(annotations["real_end_ts"]),
            )
        else:
            assert open_slice is not None, f"slice END for {name!r} at ts {ts} with nothing open"
            open_name, open_ts = open_slice
            assert open_name == name, f"slice END for {name!r} closed {open_name!r}"
            intervals[int(name.removeprefix("Process "))] = (open_ts, ts)
            open_slice = None
    assert open_slice is None, f"unclosed slice left open: {open_slice}"
    assert intervals.keys() == real.keys()

    begins = [(ts, int(name.removeprefix("Process "))) for ts, t, name, _ in emitted if t == TrackEventType.SLICE_BEGIN]
    for i, (ts, pid) in enumerate(begins):
        for later_ts, later_pid in begins[i + 1 :]:
            if later_ts != ts:
                continue
            assert intervals[pid][1] >= intervals[later_pid][1], (
                f"pids {pid} and {later_pid} share start {ts}, but {pid} is emitted first and "
                f"ends at {intervals[pid][1]}, inside {later_pid}'s end {intervals[later_pid][1]}: "
                "the outer BEGIN has to come first or the nesting opens inside-out"
            )
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


def _clip(
    spans: list[tuple[int, int, int]],
) -> list[tuple[int, int, int, int, int]]:
    """Run ``_clip_spans_to_laminar`` over *spans* -- ``[(pid, start,
    end), ...]``, one process per pid -- and drop the epoch again.

    The sweep keys on ``(pid, pid_epoch)``, and a span per pid is the
    shape every case below but the reused-pid ones is about.
    """
    clipped = _clip_spans_to_laminar([(pid, 1, start, end) for pid, start, end in spans])
    return [(pid, start, end, real_start, real_end) for pid, _pid_epoch, start, end, real_start, real_end in clipped]


class TestClipSpansToLaminar:
    """Direct tests for the ``_clip_spans_to_laminar`` sweep.

    It is a pure function, so it can be exercised without building
    packets. ``TestProcessLifetimeLaminarClipping`` below covers the same
    ground through ``finalize_perfetto_packets`` and additionally checks
    the emitted packets are ones the trace processor can pair up; these
    tests pin the sweep's own contract, including the parts the packet
    view cannot see, such as output ordering.
    """

    def test_no_spans(self) -> None:
        assert _clip([]) == []

    def test_single_span_is_unchanged(self) -> None:
        assert _clip([(100, 500, 9_000)]) == [(100, 500, 9_000, 500, 9_000)]

    def test_disjoint_spans_are_unchanged(self) -> None:
        """The first span has closed before the second opens, so the
        sweep pops it and clips nothing."""
        spans = [(100, 500, 1_000), (200, 5_000, 9_000)]
        assert _clip(spans) == [
            (100, 500, 1_000, 500, 1_000),
            (200, 5_000, 9_000, 5_000, 9_000),
        ]

    def test_nested_span_is_unchanged(self) -> None:
        """A span contained by the one still open stops the walk, which
        is the common shape of a parent outliving its child."""
        spans = [(100, 500, 9_000), (200, 1_000, 5_000)]
        assert _clip(spans) == [
            (100, 500, 9_000, 500, 9_000),
            (200, 1_000, 5_000, 1_000, 5_000),
        ]

    def test_crossing_clips_the_outer_end(self) -> None:
        """The whole point: the earlier span's end is pulled back to one
        nanosecond before the later one starts."""
        spans = [(100, 500, 1_500), (200, 1_000, 5_000)]
        assert _clip(spans) == [
            (100, 500, 999, 500, 1_500),
            (200, 1_000, 5_000, 1_000, 5_000),
        ]

    def test_touching_is_treated_as_crossing(self) -> None:
        """``A.end == B.start`` is clipped too: the relative order of an
        END and a BEGIN sharing a timestamp is not ours to control."""
        spans = [(100, 500, 1_000), (200, 1_000, 5_000)]
        assert _clip(spans) == [
            (100, 500, 999, 500, 1_000),
            (200, 1_000, 5_000, 1_000, 5_000),
        ]

    def test_equal_starts_always_nest(self) -> None:
        """The sweep's own sort puts equal starts longest-first, so they
        can never cross, which is what keeps ``start - 1`` from landing
        before the clipped span's own start."""
        spans = [(100, 500, 1_000), (200, 500, 9_000)]
        assert _clip(spans) == [
            (200, 500, 9_000, 500, 9_000),
            (100, 500, 1_000, 500, 1_000),
        ]

    def test_walk_pops_every_span_already_closed(self) -> None:
        """Two spans are open and nested when a third starts after both
        have ended; the sweep unwinds the whole stack in one walk and
        clips neither."""
        spans = [(100, 0, 100), (200, 10, 20), (300, 200, 300)]
        assert _clip(spans) == [
            (100, 0, 100, 0, 100),
            (200, 10, 20, 10, 20),
            (300, 200, 300, 200, 300),
        ]

    def test_one_span_crossed_by_two_later_spans(self) -> None:
        """The sweep is not a pairwise check of neighbours. Pid 200 nests
        inside pid 100, so comparing only adjacent spans would stop there
        and never notice that pid 300 crosses pid 100."""
        spans = [(100, 500, 5_000), (200, 1_000, 2_000), (300, 3_000, 9_000)]
        assert _clip(spans) == [
            (100, 500, 2_999, 500, 5_000),
            (200, 1_000, 2_000, 1_000, 2_000),
            (300, 3_000, 9_000, 3_000, 9_000),
        ]

    def test_chain_of_crossings_clips_each_in_turn(self) -> None:
        spans = [(100, 0, 100), (200, 10, 200), (300, 20, 300)]
        assert _clip(spans) == [
            (100, 0, 9, 0, 100),
            (200, 10, 19, 10, 200),
            (300, 20, 300, 20, 300),
        ]

    def test_clip_can_reduce_a_span_to_zero_length(self) -> None:
        """A crossing span starting one nanosecond later leaves nothing
        to draw. The span is still returned -- dropping it is the
        caller's decision, and the caller does not make it."""
        spans = [(100, 500, 5_000), (200, 501, 9_000)]
        assert _clip(spans) == [
            (100, 500, 500, 500, 5_000),
            (200, 501, 9_000, 501, 9_000),
        ]

    def test_zero_length_input_survives(self) -> None:
        """A pid observed at a single instant arrives zero-length and is
        passed through, not discarded."""
        spans = [(100, 500, 500), (200, 500, 9_000)]
        assert _clip(spans) == [
            (200, 500, 9_000, 500, 9_000),
            (100, 500, 500, 500, 500),
        ]

    def test_output_is_sorted_whatever_the_input_order(self) -> None:
        """The result comes back in the sweep's own sort order, not the
        caller's, so ``finalize_perfetto_packets`` can emit it as given."""
        spans = [(100, 0, 100), (200, 10, 200), (300, 20, 300)]
        expected = [100, 200, 300]
        for permuted in ([spans[2], spans[0], spans[1]], list(reversed(spans)), spans):
            assert [row[0] for row in _clip(permuted)] == expected

    def test_two_spans_on_one_pid_are_clipped_like_two_pids(self) -> None:
        """A reused pid brings two spans that carry the same number. The
        sweep keys on the process, so the earlier one is clipped back
        exactly as it would be if the crossing span belonged to a
        different pid."""
        spans = [(100, 1, 500, 1_500), (100, 2, 1_000, 5_000)]
        assert _clip_spans_to_laminar(spans) == [
            (100, 1, 500, 999, 500, 1_500),
            (100, 2, 1_000, 5_000, 1_000, 5_000),
        ]

    def test_two_spans_on_one_pid_that_do_not_cross_are_left_alone(self) -> None:
        """The shape reuse actually produces: one process ends before the
        next one starts, so nothing is clipped and each span bounds only
        the process it belongs to."""
        spans = [(100, 2, 5_000, 9_000), (100, 1, 500, 1_000)]
        assert _clip_spans_to_laminar(spans) == [
            (100, 1, 500, 1_000, 500, 1_000),
            (100, 2, 5_000, 9_000, 5_000, 9_000),
        ]

    @pytest.mark.parametrize("seed", range(50))
    def test_invariants_hold_for_random_spans(self, seed: int) -> None:
        """Whatever goes in, in whatever order: the result is laminar and
        sorted, and every span survives. The per-span invariants are
        named by the assertions."""
        rng = random.Random(seed)
        spans = [
            (pid, start, start + rng.randrange(0, 2_000))
            for pid in range(100, 100 + rng.randint(2, 12))
            for start in (rng.randrange(0, 2_000),)
        ]
        rng.shuffle(spans)
        clipped = _clip(spans)

        originals = {pid: (start, end) for pid, start, end in spans}
        assert {row[0] for row in clipped} == originals.keys(), "every span survives"
        assert clipped == sorted(clipped, key=lambda row: (row[1], -row[4], row[0])), (
            "the sweep sorts its own input, so the result comes back in that order"
        )
        for pid, start, end, real_start, real_end in clipped:
            assert (real_start, real_end) == originals[pid], "the observed span is passed through"
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

    def test_pid_without_process_descriptor_still_gets_a_slice(self) -> None:
        """A span is drawn for a pid that never reached ``mark_pid`` --
        one polled OK for a whole run that never collected, so it named
        no track and nothing described it. It has no process track and no
        cmdline, so the slice carries only the ``real_*`` annotations."""
        state = PerfettoTrackState()
        state.update_process_lifetime(100, 500)
        state.update_process_lifetime(100, 5_000)
        assert not state.has_pid(100)
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()
        packets = finalize_perfetto_packets(state, sequence_id=1)
        assert lifetime_slices(packets, lifetime_uuid) == [
            (
                500,
                TrackEventType.SLICE_BEGIN,
                "Process 100",
                {"real_start_ts": 500, "real_end_ts": 5_000},
            ),
            (5_000, TrackEventType.SLICE_END, "Process 100", {}),
        ]

    def test_descriptor_refuses_a_second_emission(self) -> None:
        """The descriptor emitter asserts rather than trusting its
        caller: two descriptors for one uuid are accepted silently by the
        trace processor, so nothing downstream would report it."""
        state = PerfettoTrackState()
        state.mark_process_lifetime_emitted()
        with pytest.raises(AssertionError, match="already gone out"):
            _emit_process_lifetime_track_descriptor(state, sequence_id=1)

    def test_track_is_marked_emitted_only_after_a_slice_goes_out(self) -> None:
        """A trace with no drawable span emits nothing and leaves the
        flag clear, so the guard cannot swallow a later real closeout."""
        state = PerfettoTrackState()
        assert finalize_perfetto_packets(state, sequence_id=1) == []
        assert not state.has_process_lifetime_emitted()

        state.update_process_lifetime(100, 500)
        state.update_process_lifetime(100, 5_000)
        assert finalize_perfetto_packets(state, sequence_id=1) != []
        assert state.has_process_lifetime_emitted()

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


class TestProcessLifetimeSlices:
    """The ``Processes`` track as emitted through a full convert plus
    finalize pass: one BEGIN/END pair per pid on one shared track.
    """

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
        descriptors, convert_packets, closeout = convert_items(
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
        _, packets = convert_item(100, item, state, sequence_id=1)
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
        _, packets = convert_item(100, item, state, sequence_id=1)
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
        _, packets = convert_item(100, item, state, sequence_id=1)
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
        _, convert_packets, closeout = convert_items(
            [(200, item_late_pid), (100, item_early_pid)],
            state,
            sequence_id=1,
        )
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

        assert lifetime_slices(convert_packets, lifetime_uuid) == [], (
            "convert passes must emit no Processes-track slices"
        )
        # One BEGIN/END pair per pid, in clipped-span order: pid 100
        # opens first and is closed at 999 because pid 200 crosses it,
        # then pid 200 opens and closes.
        assert lifetime_slices(closeout, lifetime_uuid) == [
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
        _, convert_packets, closeout = convert_items(
            [(100, item1), (100, item2)],
            state,
            sequence_id=1,
        )
        lifetime_uuid = state.get_or_create_process_lifetime_track_uuid()

        assert lifetime_slices(convert_packets, lifetime_uuid) == []
        # One pair only, spanning the first batch's ts_start to the
        # second batch's ts_stop.
        assert lifetime_slices(closeout, lifetime_uuid) == [
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


class TestCloseoutAtFinalize:
    """``convert_trace_events_to_perfetto`` never closes a ``Processes``
    slice; only ``finalize_perfetto_packets`` does.
    """

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
        meta: list[TraceEvent] = []
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
            *convert_item_to_trace_format(100, item1),
        ]
        events2: list[TraceEvent] = [
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
        # Calling finalize again is a no-op (the track is marked emitted).
        assert finalize_perfetto_packets(state, sequence_id=1) == []

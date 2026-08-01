"""The shared ``Processes`` track: spans, laminar clipping, emission.

One BEGIN/END pair per pid on one shared track. Slices on a Perfetto
track are a stack, so spans that overlap without nesting cannot both be
drawn; ``_clip_spans_to_laminar`` pulls the crossed end back, and
``real_start_ts`` / ``real_end_ts`` carry the observed span. See ADR-0011.

Nothing is emitted until the trace closes, because clipping needs every
pid's span at once and cannot correct a BEGIN already on the wire. The
convert pass only calls ``_record_process_lifetime``.
"""

from ..trace_event import CounterEvent, ProcessMeta, ThreadMeta, TraceEvent
from .perfetto_builders import (
    _build_debug_annotation_int,
    _build_debug_annotation_string,
    build_trace_packet,
    build_track_descriptor,
    build_track_event,
)
from .perfetto_proto import TrackEventType
from .perfetto_track_state import PerfettoTrackState

__all__ = ["finalize_perfetto_packets"]


# Name of the shared top-level Perfetto track that shows one
# TYPE_SLICE_BEGIN / TYPE_SLICE_END pair per pid, spanning that pid's
# first non-meta event to its last non-counter one. Because every pid
# shares this track, and slices on a Perfetto track have to nest, spans
# that overlap without nesting are clipped by
# `finalize_perfetto_packets`; see ADR-0011.
_PROCESS_LIFETIME_TRACK_NAME: str = "Processes"


def _emit_process_lifetime_track_descriptor(
    state: PerfettoTrackState,
    sequence_id: int,
) -> bytes:
    """Build the shared ``Processes`` track descriptor.

    Not idempotent: calling this twice emits two descriptors for the
    same uuid. ``finalize_perfetto_packets`` is the only caller and runs
    once per trace, so the guard that used to live here is gone."""
    track_uuid = state.get_or_create_process_lifetime_track_uuid()
    desc = build_track_descriptor(track_uuid, _PROCESS_LIFETIME_TRACK_NAME)
    return build_trace_packet(sequence_id, track_descriptor=desc)


def _emit_process_lifetime_slice_begin(
    pid: int,
    ts_ns: int,
    state: PerfettoTrackState,
    sequence_id: int,
    real_start_ts: int,
    real_end_ts: int,
) -> list[bytes]:
    """Emit a ``TYPE_SLICE_BEGIN`` on the shared ``Processes`` track for
    *pid* at *ts_ns*, carrying a ``cmdline`` annotation (argv joined with
    single spaces) when *state* has one recorded.

    *real_start_ts* / *real_end_ts* are the span as observed, annotated
    on **every** slice rather than only clipped ones so a consumer never
    has to check whether a clip happened. The slice's own ``ts`` and
    ``dur`` are what could be drawn; where the two disagree, these are
    the truth."""
    track_uuid = state.get_or_create_process_lifetime_track_uuid()
    debug_annotations: list[bytes] = []
    cmdline = state.get_cmdline(pid)
    if cmdline:
        debug_annotations.append(
            _build_debug_annotation_string("cmdline", " ".join(cmdline)),
        )
    debug_annotations.append(_build_debug_annotation_int("real_start_ts", real_start_ts))
    debug_annotations.append(_build_debug_annotation_int("real_end_ts", real_end_ts))
    return [
        build_trace_packet(
            sequence_id,
            timestamp=ts_ns,
            track_event=build_track_event(
                type=TrackEventType.SLICE_BEGIN,
                track_uuid=track_uuid,
                name=f"Process {pid}",
                debug_annotations=debug_annotations or None,
            ),
        )
    ]


def _emit_process_lifetime_slice_end(
    pid: int,
    ts_ns: int,
    state: PerfettoTrackState,
    sequence_id: int,
) -> bytes:
    """Emit a single ``TYPE_SLICE_END`` on the shared ``Processes`` track
    for *pid*, using *ts_ns* as the packet timestamp. The slice name is
    set to ``"Process <pid>"`` for symmetry with the BEGIN so SQL queries
    can identify the owning pid without joining to the BEGIN packet."""
    track_uuid = state.get_or_create_process_lifetime_track_uuid()
    return build_trace_packet(
        sequence_id,
        timestamp=ts_ns,
        track_event=build_track_event(
            type=TrackEventType.SLICE_END,
            track_uuid=track_uuid,
            name=f"Process {pid}",
        ),
    )


def _record_process_lifetime(
    event: TraceEvent,
    state: PerfettoTrackState,
) -> None:
    """Fold *event* into its pid's recorded ``Processes``-track span.

    Meta events are skipped, so a pid seen only through them gets no
    span and no slice. A ``CounterEvent`` moves the start but never the
    end; see ``PerfettoTrackState.update_process_lifetime``. Emits
    nothing: spans become packets at close.
    """
    if isinstance(event, (ProcessMeta, ThreadMeta)):
        return
    ts = getattr(event, "ts", None)
    if ts is None:
        return
    state.update_process_lifetime(
        event.pid,
        ts,
        extends_end=not isinstance(event, CounterEvent),
    )


def _clip_spans_to_laminar(
    spans: list[tuple[int, int, int]],
) -> list[tuple[int, int, int, int, int]]:
    """Clip *spans* so any two are disjoint or strictly nested, and
    return ``[(pid, start, end, real_start, real_end), ...]`` in input
    order. ``start``/``end`` are what the slice draws; ``real_start`` /
    ``real_end`` are the observed span, carried through untouched.

    *spans* must be sorted the way ``pop_process_lifetimes`` sorts them:
    ascending start, longer span first on a tie.

    Slices on one Perfetto track are a stack, so a crossing pair -- A
    starts first, B starts inside A and ends after it -- cannot be
    expressed. Where two spans cross, the earlier one's end is pulled
    back to one nanosecond before the later one's start; nesting is left
    alone. Spans that merely touch count as crossing, since the order of
    an END and a BEGIN sharing a timestamp is not ours to control.

    The required sort is what keeps this safe: equal starts always nest,
    so a clip only happens when ``A.start < B.start`` and ``B.start - 1``
    never lands before ``A.start``. The worst case is a zero-length span,
    which is still drawn. See ADR-0011.
    """
    ends: dict[int, int] = {}
    open_pids: list[int] = []
    for pid, start, end in spans:
        # Walk out through the spans still open at *start*, closing the
        # ones that ended before it and clipping the ones it crosses.
        # Only a span that contains this one stops the walk.
        while open_pids:
            outer_pid = open_pids[-1]
            outer_end = ends[outer_pid]
            if outer_end < start:
                open_pids.pop()
                continue
            if outer_end >= end:
                break
            ends[outer_pid] = start - 1
            open_pids.pop()
        ends[pid] = end
        open_pids.append(pid)
    return [(pid, start, ends[pid], start, end) for pid, start, end in spans]


def finalize_perfetto_packets(
    state: PerfettoTrackState,
    sequence_id: int,
) -> list[bytes]:
    """Emit every ``Processes``-track packet for the whole trace: the
    track descriptor, then a ``TYPE_SLICE_BEGIN`` / ``TYPE_SLICE_END``
    pair per pid that has a span.

    Call this once, at the end of the trace (typically the encoder's
    ``close()``). Both ends are emitted here rather than at convert time
    because keeping the track laminar needs every pid's span in hand at
    once, and a clip discovered at close cannot correct a BEGIN already
    on the wire.

    No span is dropped: a pid observed at a single instant, or clipped
    to zero, still gets a zero-duration slice, since an omission is the
    one distortion a reader cannot detect. Packets come out in stack
    order, which is how the trace processor decides which of two slices
    sharing an end timestamp closes first. The descriptor leads the list
    so it precedes its own slices.

    Safe to call with no spans, and safe to call twice; both return an
    empty list.
    """
    spans = [(pid, start, end) for pid, start, end in state.pop_process_lifetimes() if state.has_pid(pid)]
    if not spans:
        return []

    packets: list[bytes] = []
    open_spans: list[tuple[int, int]] = []
    for pid, start_ts, end_ts, real_start, real_end in _clip_spans_to_laminar(spans):
        while open_spans and open_spans[-1][1] < start_ts:
            open_pid, open_end = open_spans.pop()
            packets.append(_emit_process_lifetime_slice_end(open_pid, open_end, state, sequence_id))
        packets.extend(
            _emit_process_lifetime_slice_begin(
                pid,
                start_ts,
                state,
                sequence_id,
                real_start_ts=real_start,
                real_end_ts=real_end,
            )
        )
        open_spans.append((pid, end_ts))
    while open_spans:
        open_pid, open_end = open_spans.pop()
        packets.append(_emit_process_lifetime_slice_end(open_pid, open_end, state, sequence_id))

    return [_emit_process_lifetime_track_descriptor(state, sequence_id), *packets]

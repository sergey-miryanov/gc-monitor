"""The shared ``Processes`` track: spans, laminar clipping, emission.

One BEGIN/END pair per pid on one shared track. See ADR-0011.
"""

from ..trace_event import ProcessMeta, ThreadMeta, TraceEvent
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
# TYPE_SLICE_BEGIN / TYPE_SLICE_END pair per pid.
_PROCESS_LIFETIME_TRACK_NAME: str = "Processes"


def _emit_process_lifetime_track_descriptor(
    state: PerfettoTrackState,
    sequence_id: int,
) -> bytes:
    """Build the shared ``Processes`` track descriptor."""
    assert not state.has_process_lifetime_emitted(), (
        "the Processes track descriptor has already gone out for this trace"
    )
    track_uuid = state.get_or_create_process_lifetime_track_uuid()
    desc = build_track_descriptor(track_uuid, _PROCESS_LIFETIME_TRACK_NAME)
    return build_trace_packet(sequence_id, track_descriptor=desc)


def _emit_process_lifetime_slice(
    pid: int,
    start_ts: int,
    end_ts: int,
    state: PerfettoTrackState,
    sequence_id: int,
    real_start_ts: int,
    real_end_ts: int,
) -> list[bytes]:
    """Emit the ``TYPE_SLICE_BEGIN`` / ``TYPE_SLICE_END`` pair drawing
    *pid*'s slice over ``[start_ts, end_ts]``, BEGIN first: the trace
    processor breaks timestamp ties by position in the sequence, so a
    zero-length span with its END first reads as ``dur = -1``.

    The BEGIN carries a ``cmdline`` annotation when *state* has one, plus
    *real_start_ts* / *real_end_ts*: the span as observed, annotated on
    **every** slice rather than only clipped ones so a consumer never has
    to check whether a clip happened. Where those and the drawn ``ts`` /
    ``dur`` disagree, the annotations are the truth.

    The END repeats the name, and that is load-bearing: the trace
    processor matches a named END to the BEGIN carrying that name,
    force-closing anything above it."""
    track_uuid = state.get_or_create_process_lifetime_track_uuid()
    name = f"Process {pid}"
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
            timestamp=start_ts,
            track_event=build_track_event(
                type=TrackEventType.SLICE_BEGIN,
                track_uuid=track_uuid,
                name=name,
                debug_annotations=debug_annotations,
            ),
        ),
        build_trace_packet(
            sequence_id,
            timestamp=end_ts,
            track_event=build_track_event(
                type=TrackEventType.SLICE_END,
                track_uuid=track_uuid,
                name=name,
            ),
        ),
    ]


def _record_process_lifetime(
    event: TraceEvent,
    state: PerfettoTrackState,
) -> None:
    """Fold *event* into its pid's recorded ``Processes``-track span.

    Every non-meta event widens the span in both directions, counters
    included: a timestamped event is evidence the process existed at
    that instant, whatever kind of event it is. Meta events carry no
    timestamp, so a pid seen only through them gets no span and no
    slice. Emits nothing: spans become packets at close.
    """
    if isinstance(event, (ProcessMeta, ThreadMeta)):
        return
    ts = getattr(event, "ts", None)
    if ts is None:
        return
    state.update_process_lifetime(event.pid, ts)


def _clip_spans_to_laminar(
    spans: list[tuple[int, int, int]],
) -> list[tuple[int, int, int, int, int]]:
    """Clip *spans* so any two are disjoint or strictly nested, and
    return ``[(pid, start, end, real_start, real_end), ...]`` sorted by
    ascending start, longer span first on a tie, then pid. ``start`` /
    ``end`` are what the slice draws; ``real_start`` / ``real_end`` are
    the observed span, carried through untouched. See ADR-0011.
    """
    spans = sorted(spans, key=lambda span: (span[1], -span[2], span[0]))
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
    track descriptor, then one slice per pid that has a span. Call this
    once, at the end of the trace (typically the encoder's ``close()``).

    Every pid with a span gets a slice, including one the monitor loop
    only ever reported as live. Such a pid has no process descriptor and
    no cmdline -- nothing but the span itself -- and drawing it anyway is
    the point of monitor-reported liveness: a process gcmon polled for a
    whole run without it ever collecting is precisely what would
    otherwise leave no trace at all.

    No span is dropped: a pid observed at a single instant, or clipped to
    zero, still gets a zero-duration slice. Slices go out in the order
    ``_clip_spans_to_laminar`` returns. See ADR-0011.

    Safe to call with no spans, and safe to call twice; both return an
    empty list. The ``_process_lifetime_emitted`` flag on *state* guards
    the second call, and it guards the whole track: the descriptor is not
    idempotent on its own.
    """
    if state.has_process_lifetime_emitted():
        return []
    spans = state.get_process_lifetimes()
    if not spans:
        return []

    packets: list[bytes] = []
    for pid, start_ts, end_ts, real_start, real_end in _clip_spans_to_laminar(spans):
        packets.extend(
            _emit_process_lifetime_slice(
                pid,
                start_ts,
                end_ts,
                state,
                sequence_id,
                real_start_ts=real_start,
                real_end_ts=real_end,
            )
        )

    descriptor = _emit_process_lifetime_track_descriptor(state, sequence_id)
    state.mark_process_lifetime_emitted()
    return [descriptor, *packets]

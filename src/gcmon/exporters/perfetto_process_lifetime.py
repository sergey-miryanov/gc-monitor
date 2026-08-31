"""The shared ``Processes`` track: spans, laminar clipping, emission.

One BEGIN/END pair per process on one shared track. See ADR-0011.
"""

from typing import NamedTuple

from ..model.process import Process
from ..model.trace_event import Slice, TraceEvent
from .perfetto_builders import (
    _build_debug_annotation_int,
    _build_debug_annotation_string,
    build_trace_packet,
    build_track_descriptor,
    build_track_event,
)
from .perfetto_proto import TrackEventType
from .perfetto_track_state import PerfettoTrackState, ProcessSpan

__all__ = ["finalize_perfetto_packets", "process_track_name"]


class ClippedSpan(NamedTuple):
    """A :class:`ProcessSpan` as the slice draws it, and as it was
    observed.

    Clipping moves ``start_ts`` / ``end_ts`` and leaves ``real_start_ts``
    / ``real_end_ts`` alone, so where the two disagree the observed pair
    is the truth (ADR-0011).
    """

    process: Process
    start_ts: int
    end_ts: int
    real_start_ts: int
    real_end_ts: int


# Name of the shared top-level Perfetto track that shows one
# TYPE_SLICE_BEGIN / TYPE_SLICE_END pair per process.
_PROCESS_LIFETIME_TRACK_NAME: str = "Processes"


def process_track_name(process: Process) -> str:
    """What *process* is called, on its own row and on its span here.

    The two have to be equal: the epoch reaches SQL through the name and
    through no column of its own (ADR-0011).
    """
    return f"Process {process}"


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
    span: ClippedSpan,
    state: PerfettoTrackState,
    sequence_id: int,
) -> list[bytes]:
    """Emit the ``TYPE_SLICE_BEGIN`` / ``TYPE_SLICE_END`` pair drawing *span*,
    BEGIN first: the trace processor breaks timestamp ties by position in
    the sequence, so a zero-length span with its END first reads as
    ``dur = -1``.

    The BEGIN carries ``pid_epoch``, *real_start_ts* and *real_end_ts*, plus
    a ``cmdline`` annotation where the process has one. All three go on
    **every** slice, so a consumer never has to check whether a clip
    happened; where they and the drawn ``ts`` / ``dur`` disagree, they are
    the truth.

    The name carries the epoch as a ``#N`` suffix from the second process
    on a pid, and the END repeats it: END matching is by name, so two
    spans on one pid would otherwise close each other (ADR-0011)."""
    track_uuid = state.get_or_create_process_lifetime_track_uuid()
    name = process_track_name(span.process)
    debug_annotations: list[bytes] = []
    cmdline = state.get_cmdline(span.process)
    if cmdline:
        debug_annotations.append(
            _build_debug_annotation_string("cmdline", " ".join(cmdline)),
        )
    debug_annotations.append(_build_debug_annotation_int("pid_epoch", span.process.pid_epoch))
    debug_annotations.append(_build_debug_annotation_int("real_start_ts", span.real_start_ts))
    debug_annotations.append(_build_debug_annotation_int("real_end_ts", span.real_end_ts))
    return [
        build_trace_packet(
            sequence_id,
            timestamp=span.start_ts,
            track_event=build_track_event(
                type=TrackEventType.SLICE_BEGIN,
                track_uuid=track_uuid,
                name=name,
                debug_annotations=debug_annotations,
            ),
        ),
        build_trace_packet(
            sequence_id,
            timestamp=span.end_ts,
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

    Every event widens the span in both directions, counters included: a
    timestamped event is evidence the process existed at that instant,
    whatever kind it is. A slice is evidence at both its ends, so it is
    folded in twice. Emits nothing: spans become packets at close.
    """
    process = event.track.process
    if isinstance(event, Slice):
        state.update_process_lifetime(process, event.ts_start)
        state.update_process_lifetime(process, event.ts_stop)
    else:
        state.update_process_lifetime(process, event.ts)


def _clip_spans_to_laminar(spans: list[ProcessSpan]) -> list[ClippedSpan]:
    """Clip *spans* so any two are disjoint or strictly nested, sorted by
    ascending start, longer span first on a tie, then process. The drawn
    pair moves; the observed pair is carried through untouched. See
    ADR-0011.
    """
    spans = sorted(spans, key=lambda span: (span.start_ts, -span.end_ts, span.process))
    ends: dict[Process, int] = {}
    open_processes: list[Process] = []
    for process, start, end in spans:
        # Walk out through the spans still open at *start*, closing the
        # ones that ended before it and clipping the ones it crosses.
        # Only a span that contains this one stops the walk.
        while open_processes:
            outer = open_processes[-1]
            outer_end = ends[outer]
            if outer_end < start:
                open_processes.pop()
                continue
            if outer_end >= end:
                break
            ends[outer] = start - 1
            open_processes.pop()
        ends[process] = end
        open_processes.append(process)
    return [ClippedSpan(process, start, ends[process], start, end) for process, start, end in spans]


def finalize_perfetto_packets(
    state: PerfettoTrackState,
    sequence_id: int,
) -> list[bytes]:
    """Emit every ``Processes``-track packet for the whole trace: the
    track descriptor, then one slice per process that has a span. Call
    this once, at the end of the trace (typically the encoder's
    ``close()``).

    Every process with a span gets a slice, including one the monitor loop
    only ever reported as live: it has no process descriptor and no
    cmdline, nothing but the span, and drawing it anyway is the point of
    monitor-reported liveness.

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
    for span in _clip_spans_to_laminar(spans):
        packets.extend(_emit_process_lifetime_slice(span, state, sequence_id))

    descriptor = _emit_process_lifetime_track_descriptor(state, sequence_id)
    state.mark_process_lifetime_emitted()
    return [descriptor, *packets]

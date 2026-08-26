"""Track layout policy and the GC-to-Perfetto conversion pass.

``convert_trace_events_to_perfetto`` maps ``TraceEvent`` objects from the
shared ``trace_converter`` to Perfetto packets; the ``_emit_*`` helpers
place, parent and order each track.

The module is also the package's public face, re-exporting the enums,
builders, ``PerfettoTrackState`` and ``finalize_perfetto_packets`` so an
importer needs one name.
"""

from collections.abc import Sequence

from ..model.trace_event import (
    Counter,
    Instant,
    InterpreterTrack,
    LossTrack,
    ProcessTrack,
    Slice,
    TraceEvent,
    Track,
)
from .perfetto_builders import (
    _args_to_debug_annotations,
    _make_counter_event,
    _make_slice_begin,
    _make_slice_end,
    build_trace,
    build_trace_packet,
    build_track_descriptor,
    build_track_event,
)
from .perfetto_process_lifetime import (
    _record_process_lifetime,
    finalize_perfetto_packets,
)
from .perfetto_proto import (
    ChildTracksOrdering,
    CounterDescriptorField,
    DebugAnnotationField,
    ProcessDescriptorField,
    ProcessOrdering,
    ThreadDescriptorField,
    ThreadOrdering,
    TraceField,
    TracePacketField,
    TrackDescriptorField,
    TrackEventField,
    TrackEventType,
)
from .perfetto_track_state import PerfettoTrackState

__all__ = [
    "ChildTracksOrdering",
    "CounterDescriptorField",
    "DebugAnnotationField",
    "PerfettoTrackState",
    "ProcessDescriptorField",
    "ProcessOrdering",
    "ThreadDescriptorField",
    "ThreadOrdering",
    "TraceField",
    "TracePacketField",
    "TrackDescriptorField",
    "TrackEventField",
    "TrackEventType",
    "build_trace",
    "build_trace_packet",
    "build_track_descriptor",
    "build_track_event",
    "convert_trace_events_to_perfetto",
    "finalize_perfetto_packets",
]


_COUNTER_RANKS: dict[str, int] = {
    "heap_size": 0,
    "rss": 1,
    "collected": 2,
    "uncollectable": 3,
    "candidates": 4,
    "duration": 5,
    "increment_size": 6,
    "alive_size": 7,
    "finalized_garbage_count": 8,
    "deleted_garbage_count": 9,
    "clear_weakrefs_count": 10,
}

# A counter an interpreter owns that is nonetheless drawn a level up, beside
# the process's own counters rather than inside its `GC Metrics` group
# (ADR-0004). The process track is OS-scoped, so the trace processor drops
# `sibling_order_rank` for these and their position in the UI is a heuristic.
#
# `rss` is not here: a `ProcessTrack` owns it, so parenting it to the process
# row is its identity rather than a policy.
_TOPLEVEL_COUNTER_METRICS: frozenset[str] = frozenset({"heap_size"})

_COUNTER_GROUP_NAME: str = "GC Metrics"

_LOSS_TRACK_NAME: str = "GC Loss"
# Below the interpreter's own thread track, which ranks 0.
_LOSS_TRACK_RANK: int = 1

_START_PROCESS_INSTANT_NAME: str = "Start Process"


def _emit_root_descriptor(
    state: PerfettoTrackState,
    sequence_id: int,
) -> list[bytes]:
    """Build the root ``TrackDescriptor`` (``uuid = 0``), once per trace.

    Its ``process_ordering`` and ``thread_ordering`` hints are what make the
    Perfetto UI honor ``sibling_order_rank`` on top-level process and thread
    tracks. It carries no ``name`` and no ``process``, ``thread`` or
    ``counter`` sub-message, so the trace processor draws no row for it.

    The UI reads the hints only on the canary channel of ``ui.perfetto.dev``
    (Flags -> Release channel -> Canary), and a trace processor older than
    0.57 ignores them and falls back to its own ordering. gcmon writes them
    whatever the reader, so a trace stays forward-compatible.
    """
    if state.has_root_descriptor():
        return []
    state.mark_root_descriptor_emitted()
    desc = build_track_descriptor(
        uuid=0,
        name="",
        process_ordering=ProcessOrdering.EXPLICIT,
        thread_ordering=ThreadOrdering.EXPLICIT,
    )
    return [build_trace_packet(sequence_id, track_descriptor=desc)]


def _emit_process_descriptor(
    pid: int,
    state: PerfettoTrackState,
    sequence_id: int,
    sibling_order_rank: int | None = None,
    start_timestamp_ns: int | None = None,
) -> list[bytes]:
    """Build a process track descriptor if not already emitted for *pid*.

    *sibling_order_rank* orders this process against the other process
    tracks, which the UI honors only when the root descriptor carries
    ``process_ordering = PROCESS_ORDERING_EXPLICIT``; see
    ``_emit_root_descriptor``.

    *start_timestamp_ns* goes on the ``process`` sub-message. It is *pid*'s
    first event in nanoseconds, the same timestamp the rank comes from.
    """
    if state.has_pid(pid):
        return []
    state.mark_pid(pid)
    proc_uuid = state.get_process_track_uuid(pid)
    cmdline = state.get_cmdline(pid)
    desc = build_track_descriptor(
        proc_uuid,
        f"Process {pid}",
        pid=pid,
        child_ordering=ChildTracksOrdering.EXPLICIT,
        sibling_order_rank=sibling_order_rank,
        cmdline=cmdline,
        description=" ".join(cmdline) if cmdline else None,
        start_timestamp_ns=start_timestamp_ns,
    )
    return [build_trace_packet(sequence_id, track_descriptor=desc)]


def _emit_start_process_marker(
    pid: int,
    ts_ns: int,
    state: PerfettoTrackState,
    sequence_id: int,
) -> list[bytes]:
    """Emit a single dur=0 ``Start Process`` marker on the process track.

    Idempotent per pid, and stamped with the first event that pid produced.
    The Perfetto UI hides a track holding no events, and with it the process
    track's ``description``, the joined cmdline. One marker keeps the
    description visible however few ``Instant`` the caller sent.
    """
    if state.has_start_process_marker(pid):
        return []
    state.mark_start_process_marker(pid)
    proc_uuid = state.get_process_track_uuid(pid)
    return [
        build_trace_packet(
            sequence_id,
            timestamp=ts_ns,
            track_event=build_track_event(
                type=TrackEventType.INSTANT,
                track_uuid=proc_uuid,
                name=_START_PROCESS_INSTANT_NAME,
            ),
        )
    ]


def _emit_thread_descriptor(
    track: InterpreterTrack,
    state: PerfettoTrackState,
    sequence_id: int,
) -> list[bytes]:
    """Build *track*'s thread track descriptor if not already emitted."""
    if state.has_track(track):
        return []
    state.mark_track(track)
    iid = track.iid
    desc = build_track_descriptor(
        state.get_track_uuid(track),
        f"Thread {iid}",
        pid=track.pid,
        tid=track.pid if iid == 0 else iid,
        parent_uuid=state.get_process_track_uuid(track.pid),
        sibling_order_rank=0,
        thread_name=f"Thread {iid}",
    )
    return [build_trace_packet(sequence_id, track_descriptor=desc)]


def _emit_loss_descriptor(
    track: LossTrack,
    state: PerfettoTrackState,
    sequence_id: int,
) -> list[bytes]:
    """Build *track*'s GC Loss track descriptor, once.

    A plain custom track rather than a thread: a ``LossTrack`` names an
    interpreter but no OS thread, and a ``thread`` sub-message would describe
    one that does not exist.
    """
    if state.has_track(track):
        return []
    state.mark_track(track)
    desc = build_track_descriptor(
        state.get_track_uuid(track),
        f"{_LOSS_TRACK_NAME} {track.iid}",
        parent_uuid=state.get_process_track_uuid(track.pid),
        sibling_order_rank=_LOSS_TRACK_RANK,
    )
    return [build_trace_packet(sequence_id, track_descriptor=desc)]


def _emit_counter_group_descriptor(
    track: Track,
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[int, list[bytes]]:
    """Build *track*'s GC Metrics grouping track descriptor.

    The trace processor ignores ``child_ordering`` and ``sibling_order_rank``
    on an OS-scoped process or thread track and honors them on a plain custom
    one. So the group carries no ``process`` or ``thread`` field, the counter
    tracks hang off the group, and the group hangs off the process track
    (ADR-0003).
    """
    if state.has_counter_group_track(track):
        return state.get_or_create_counter_group_track_uuid(track), []
    group_uuid = state.get_or_create_counter_group_track_uuid(track)
    desc = build_track_descriptor(
        group_uuid,
        _COUNTER_GROUP_NAME,
        parent_uuid=state.get_process_track_uuid(track.pid),
        child_ordering=ChildTracksOrdering.EXPLICIT,
        sibling_order_rank=0,
    )
    return group_uuid, [build_trace_packet(sequence_id, track_descriptor=desc)]


def _emit_counter_track_descriptor(
    track: Track,
    metric: str,
    display_name: str,
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[int, list[bytes]]:
    """Build a counter track descriptor if not already emitted.

    A counter the process owns, and one an interpreter owns whose metric is in
    ``_TOPLEVEL_COUNTER_METRICS``, hangs off the process track and renders at
    the top level. Every other counter hangs off its owner's GC Metrics group,
    where the trace processor and the UI honor its ``_COUNTER_RANKS`` entry.

    *display_name* is the track name on the wire and identifies the track
    within *track*; *metric* is what the rank and the shared y axis are keyed
    on, so ``G0 collected`` and ``G1 collected`` share a scale.
    """
    if isinstance(track, ProcessTrack) or metric in _TOPLEVEL_COUNTER_METRICS:
        if state.has_counter_track(track, display_name):
            return state.get_or_create_counter_track_uuid(track, display_name), []
        ctr_uuid = state.get_or_create_counter_track_uuid(track, display_name)
        desc = build_track_descriptor(
            ctr_uuid,
            display_name,
            parent_uuid=state.get_process_track_uuid(track.pid),
            is_counter=True,
            sibling_order_rank=_COUNTER_RANKS.get(metric, 0),
        )
        return ctr_uuid, [build_trace_packet(sequence_id, track_descriptor=desc)]
    group_uuid, group_packets = _emit_counter_group_descriptor(track, state, sequence_id)
    if state.has_counter_track(track, display_name):
        ctr_uuid = state.get_or_create_counter_track_uuid(track, display_name)
        return ctr_uuid, group_packets
    ctr_uuid = state.get_or_create_counter_track_uuid(track, display_name)
    desc = build_track_descriptor(
        ctr_uuid,
        display_name,
        parent_uuid=group_uuid,
        is_counter=True,
        sibling_order_rank=_COUNTER_RANKS.get(metric, 0),
        y_axis_share_key=metric,
    )
    return ctr_uuid, [*group_packets, build_trace_packet(sequence_id, track_descriptor=desc)]


def _emit_track_descriptors(
    track: Track,
    state: PerfettoTrackState,
    sequence_id: int,
    ranks: dict[int, int],
) -> list[bytes]:
    """Every descriptor *track* needs that has not gone out yet, parent
    first.

    The pid's process descriptor whichever kind of track this is, then the
    track's own where it has one of its own to write. A ``ProcessTrack`` has
    none: the process descriptor *is* its descriptor.
    """
    pid = track.pid
    descriptors = _emit_process_descriptor(
        pid,
        state,
        sequence_id,
        sibling_order_rank=ranks.get(pid),
        start_timestamp_ns=state.get_process_lifetime_start_ts(pid),
    )
    if isinstance(track, InterpreterTrack):
        descriptors.extend(_emit_thread_descriptor(track, state, sequence_id))
    elif isinstance(track, LossTrack):
        descriptors.extend(_emit_loss_descriptor(track, state, sequence_id))
    return descriptors


def convert_trace_events_to_perfetto(
    events: Sequence[TraceEvent],
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[list[bytes], list[bytes]]:
    """Convert a list of ``TraceEvent`` objects to Perfetto protobuf packets.

    A track's descriptor goes out because an event named that track, ahead of
    the packet that named it. No producer sends metadata first, and none can
    forget to.

    Returns ``(descriptors, packets)``, two lists of encoded ``TracePacket``
    bytes ready for ``build_trace``. The first call on a given *state* also
    emits the root descriptor.

    Each process descriptor carries a ``sibling_order_rank`` and a
    ``process.start_timestamp_ns``, both taken from the pid's first event.
    *state* accumulates those across batches, and the pre-pass below folds
    this batch in before the main loop, so a pid described in this batch is
    ranked against the events of it. Both fields are always present: a
    descriptor goes out only for a pid that named a track, which is a pid
    the pre-pass has folded in.

    ``Processes``-track slices go out at trace close instead, from
    ``finalize_perfetto_packets``.
    """
    descriptors: list[bytes] = []
    packets: list[bytes] = []

    if events:
        for event in events:
            _record_process_lifetime(event, state)
        descriptors.extend(_emit_root_descriptor(state, sequence_id))

    ranks = state.get_process_track_ranks()

    for event in events:
        pid = event.track.pid
        descriptors.extend(_emit_track_descriptors(event.track, state, sequence_id, ranks))

        if isinstance(event, Slice):
            _maybe_emit_start_process_marker(pid, event.ts_start, state, sequence_id, packets)
            track_uuid = state.get_track_uuid(event.track)
            # An adjacent pair, not interleaved into stack order: the trace
            # processor sorts by timestamp and builds the nesting itself.
            # BEGIN first, so a zero-length slice reads as ``dur = 0`` rather
            # than ``-1``. See ADR-0011 for the pattern and ADR-0024 for why
            # an anonymous END does not need the naming ADR-0011 relies on.
            packets.append(
                build_trace_packet(
                    sequence_id,
                    timestamp=event.ts_start,
                    track_event=_make_slice_begin(
                        track_uuid,
                        event.name,
                        [event.cat],
                        _args_to_debug_annotations(event.args),
                    ),
                )
            )
            packets.append(
                build_trace_packet(
                    sequence_id,
                    timestamp=event.ts_stop,
                    track_event=_make_slice_end(track_uuid),
                )
            )

        elif isinstance(event, Instant):
            _maybe_emit_start_process_marker(pid, event.ts, state, sequence_id, packets)
            proc_uuid = state.get_process_track_uuid(pid)
            packets.append(
                build_trace_packet(
                    sequence_id,
                    timestamp=event.ts,
                    track_event=build_track_event(
                        type=TrackEventType.INSTANT,
                        track_uuid=proc_uuid,
                        name=event.name,
                        debug_annotations=_args_to_debug_annotations(event.args),
                    ),
                )
            )

        elif isinstance(event, Counter):
            _maybe_emit_start_process_marker(pid, event.ts, state, sequence_id, packets)
            ctr_uuid, desc_bytes = _emit_counter_track_descriptor(
                event.track,
                event.metric,
                event.display_name,
                state,
                sequence_id,
            )
            descriptors.extend(desc_bytes)
            packets.append(
                build_trace_packet(
                    sequence_id,
                    timestamp=event.ts,
                    track_event=_make_counter_event(ctr_uuid, event.value),
                )
            )

    return descriptors, packets


def _maybe_emit_start_process_marker(
    pid: int,
    ts: int,
    state: PerfettoTrackState,
    sequence_id: int,
    packets: list[bytes],
) -> None:
    """Place *pid*'s ``Start Process`` marker at *ts*, if it has not gone
    out yet.

    Takes the timestamp rather than the event, since a `Slice` keeps its
    in `ts_start` and the other two in `ts`, and the caller is already
    inside the branch that knows which.

    The process track is described by the time this runs whatever the
    event: the caller emits its track descriptors first.
    """
    if state.has_start_process_marker(pid):
        return
    packets.extend(_emit_start_process_marker(pid, ts, state, sequence_id))

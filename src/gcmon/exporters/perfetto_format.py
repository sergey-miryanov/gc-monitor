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
    LOSS_TID_BASE,
    BeginEvent,
    CounterEvent,
    EndEvent,
    InstantEvent,
    ProcessMeta,
    ThreadMeta,
    TraceEvent,
    loss_iid,
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

# These parent straight to the process track rather than to the group, so
# they render as top-level counters (ADR-0004). The process track is
# OS-scoped, so the trace processor drops `sibling_order_rank` for them and
# their position in the UI is a heuristic.
_TOPLEVEL_COUNTER_METRICS: frozenset[str] = frozenset({"heap_size", "rss"})

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
    first non-meta event in nanoseconds, the same timestamp the rank comes
    from.
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

    Idempotent per pid, and stamped with the first non-meta event that pid
    produced. The Perfetto UI hides a track holding no events, and with it
    the process track's ``description``, the joined cmdline. One marker
    keeps the description visible however few ``InstantEvent`` the caller
    sent.
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
    pid: int,
    iid: int,
    state: PerfettoTrackState,
    sequence_id: int,
) -> list[bytes]:
    """Build a thread track descriptor if not already emitted for *(pid, iid)*."""
    if state.has_tid(pid, iid):
        return []
    state.mark_tid(pid, iid)
    thread_uuid = state.get_thread_track_uuid(pid, iid)
    desc = build_track_descriptor(
        thread_uuid,
        f"Thread {iid}",
        pid=pid,
        tid=pid if iid == 0 else iid,
        parent_uuid=state.get_process_track_uuid(pid),
        sibling_order_rank=0,
        thread_name=f"Thread {iid}",
    )
    return [build_trace_packet(sequence_id, track_descriptor=desc)]


def _emit_loss_descriptor(
    pid: int,
    tid: int,
    state: PerfettoTrackState,
    sequence_id: int,
) -> list[bytes]:
    """Build the per-``(pid, iid)`` GC Loss track descriptor, once.

    Returns nothing for a tid that is not a loss track, so the slice branches
    can call this without checking first.

    Nothing else describes this track. A thread track takes its descriptor
    from a ``ThreadMeta``, and gcmon writes none for a negative tid, the same
    silence that keeps Perfetto from drawing the row as a thread.

    A plain custom track, like the counter group. ``_emit_thread_descriptor``
    would name it ``Thread -2`` and put ``tid = -2`` on a thread sub-message,
    describing an OS thread that does not exist. A ``TraceEvent`` carries the
    tid alone, so the interpreter comes back out of the sentinel.
    """
    if tid > LOSS_TID_BASE or state.has_tid(pid, tid):
        return []
    state.mark_tid(pid, tid)
    desc = build_track_descriptor(
        state.get_thread_track_uuid(pid, tid),
        f"{_LOSS_TRACK_NAME} {loss_iid(tid)}",
        parent_uuid=state.get_process_track_uuid(pid),
        sibling_order_rank=_LOSS_TRACK_RANK,
    )
    return [build_trace_packet(sequence_id, track_descriptor=desc)]


def _emit_counter_group_descriptor(
    pid: int,
    iid: int,
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[int, list[bytes]]:
    """Build the per-(pid, iid) GC Metrics grouping track descriptor.

    The trace processor ignores ``child_ordering`` and ``sibling_order_rank``
    on an OS-scoped process or thread track and honors them on a plain custom
    one. So the group carries no ``process`` or ``thread`` field, the counter
    tracks hang off the group, and the group hangs off the process track
    (ADR-0003).
    """
    if state.has_counter_group_track(pid, iid):
        return state.get_or_create_counter_group_track_uuid(pid, iid), []
    group_uuid = state.get_or_create_counter_group_track_uuid(pid, iid)
    desc = build_track_descriptor(
        group_uuid,
        _COUNTER_GROUP_NAME,
        parent_uuid=state.get_process_track_uuid(pid),
        child_ordering=ChildTracksOrdering.EXPLICIT,
        sibling_order_rank=0,
    )
    return group_uuid, [build_trace_packet(sequence_id, track_descriptor=desc)]


def _emit_counter_track_descriptor(
    pid: int,
    iid: int,
    metric: str,
    display_name: str,
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[int, list[bytes]]:
    """Build a counter track descriptor if not already emitted.

    A metric in ``_TOPLEVEL_COUNTER_METRICS`` hangs off the process track and
    renders at the top level. Every other counter hangs off the pid's GC
    Metrics group, where the trace processor and the UI honor its
    ``_COUNTER_RANKS`` entry.

    *display_name* is the track name on the wire and identifies the track
    within *(pid, iid)*; *metric* is what the rank and the shared y axis are
    keyed on, so ``G0 collected`` and ``G1 collected`` share a scale.
    """
    if metric in _TOPLEVEL_COUNTER_METRICS:
        if state.has_counter_track(pid, iid, display_name):
            return state.get_or_create_counter_track_uuid(pid, iid, display_name), []
        ctr_uuid = state.get_or_create_counter_track_uuid(pid, iid, display_name)
        desc = build_track_descriptor(
            ctr_uuid,
            display_name,
            parent_uuid=state.get_process_track_uuid(pid),
            is_counter=True,
            sibling_order_rank=_COUNTER_RANKS.get(metric, 0),
        )
        return ctr_uuid, [build_trace_packet(sequence_id, track_descriptor=desc)]
    group_uuid, group_packets = _emit_counter_group_descriptor(pid, iid, state, sequence_id)
    if state.has_counter_track(pid, iid, display_name):
        ctr_uuid = state.get_or_create_counter_track_uuid(pid, iid, display_name)
        return ctr_uuid, group_packets
    ctr_uuid = state.get_or_create_counter_track_uuid(pid, iid, display_name)
    desc = build_track_descriptor(
        ctr_uuid,
        display_name,
        parent_uuid=group_uuid,
        is_counter=True,
        sibling_order_rank=_COUNTER_RANKS.get(metric, 0),
        y_axis_share_key=metric,
    )
    return ctr_uuid, [*group_packets, build_trace_packet(sequence_id, track_descriptor=desc)]


def convert_trace_events_to_perfetto(
    events: Sequence[TraceEvent],
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[list[bytes], list[bytes]]:
    """Convert a list of ``TraceEvent`` objects to Perfetto protobuf packets.

    The caller must include ``ProcessMeta`` and ``ThreadMeta`` events, at
    least once per pid and tid, or no track descriptor goes out.
    ``PerfettoExporter`` does that itself.

    Returns ``(descriptors, packets)``, two lists of encoded ``TracePacket``
    bytes ready for ``build_trace``. The first call on a given *state* also
    emits the root descriptor.

    Each process descriptor carries a ``sibling_order_rank`` and a
    ``process.start_timestamp_ns``, both taken from the pid's first non-meta
    event. *state* accumulates those across batches, and the pre-pass below
    folds this batch in before the main loop, so a pid whose first event
    shares a batch with its ``ProcessMeta`` still gets a rank. A pid with no
    recorded span gets neither field.

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
        pid = event.pid

        if isinstance(event, ProcessMeta):
            descriptors.extend(
                _emit_process_descriptor(
                    pid,
                    state,
                    sequence_id,
                    sibling_order_rank=ranks.get(pid),
                    start_timestamp_ns=state.get_process_lifetime_start_ts(pid),
                )
            )

        # A `ThreadMeta` arriving before its pid's `ProcessMeta` still gets a
        # process track: one descriptor goes out per pid, whichever event
        # asks for it first.
        elif isinstance(event, ThreadMeta):
            descriptors.extend(
                _emit_process_descriptor(
                    pid,
                    state,
                    sequence_id,
                    sibling_order_rank=ranks.get(pid),
                    start_timestamp_ns=state.get_process_lifetime_start_ts(pid),
                )
            )
            descriptors.extend(_emit_thread_descriptor(pid, event.tid, state, sequence_id))

        elif isinstance(event, BeginEvent):
            _maybe_emit_start_process_marker(event, state, sequence_id, packets)
            descriptors.extend(_emit_loss_descriptor(pid, event.tid, state, sequence_id))
            thread_uuid = state.get_thread_track_uuid(pid, event.tid)
            annotations = _args_to_debug_annotations(event.args)
            packets.append(
                build_trace_packet(
                    sequence_id,
                    timestamp=event.ts,
                    track_event=_make_slice_begin(
                        thread_uuid,
                        event.name,
                        [event.cat],
                        annotations,
                    ),
                )
            )

        elif isinstance(event, EndEvent):
            _maybe_emit_start_process_marker(event, state, sequence_id, packets)
            descriptors.extend(_emit_loss_descriptor(pid, event.tid, state, sequence_id))
            thread_uuid = state.get_thread_track_uuid(pid, event.tid)
            packets.append(
                build_trace_packet(
                    sequence_id,
                    timestamp=event.ts,
                    track_event=_make_slice_end(thread_uuid),
                )
            )

        elif isinstance(event, InstantEvent):
            _maybe_emit_start_process_marker(event, state, sequence_id, packets)
            proc_uuid = state.get_process_track_uuid(pid)
            packets.append(
                build_trace_packet(
                    sequence_id,
                    timestamp=event.ts,
                    track_event=build_track_event(
                        type=TrackEventType.INSTANT,
                        track_uuid=proc_uuid,
                        name=event.name,
                    ),
                )
            )

        elif isinstance(event, CounterEvent):
            _maybe_emit_start_process_marker(event, state, sequence_id, packets)
            ctr_uuid, desc_bytes = _emit_counter_track_descriptor(
                pid,
                event.tid,
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
    event: TraceEvent,
    state: PerfettoTrackState,
    sequence_id: int,
    packets: list[bytes],
) -> None:
    """Place the pid's ``Start Process`` marker at *event*, if the process
    track is already described and the marker is not."""
    if not state.has_pid(event.pid) or state.has_start_process_marker(event.pid):
        return
    ts = getattr(event, "ts", 0)
    packets.extend(_emit_start_process_marker(event.pid, ts, state, sequence_id))

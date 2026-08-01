"""Track layout policy and the GC-to-Perfetto conversion pass.

``convert_trace_events_to_perfetto`` maps ``TraceEvent`` objects from the
shared ``trace_converter`` to Perfetto packets; the ``_emit_*`` helpers
decide where each track goes and how it is parented and ordered.

Also the package's public face: it re-exports the enums, builders,
``PerfettoTrackState`` and ``finalize_perfetto_packets``, so importers
need to know only this name.
"""

from collections.abc import Sequence

from ..trace_event import (
    BeginEvent,
    CounterEvent,
    EndEvent,
    InstantEvent,
    ProcessMeta,
    ThreadMeta,
    TraceEvent,
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

# Metrics listed here are parented directly to the process track (outside the
# GC Metrics group) so they render as top-level counters rather than inside
# the group. NOTE: because the process track is OS-scoped, trace processor
# drops `sibling_order_rank` for these — their UI position is heuristic, not
# guaranteed.
_TOPLEVEL_COUNTER_METRICS: frozenset[str] = frozenset({"heap_size", "rss"})

# Name of the non-OS-scoped grouping track that holds all GC counter tracks
# for a given (pid, tid). Parenting counters to this group (instead of
# directly to the process track) is what makes Perfetto actually honour
# `child_ordering`/`sibling_order_rank`: trace processor ignores those fields
# on OS-scoped (process/thread) tracks, but honors them on plain custom
# child tracks.
_COUNTER_GROUP_NAME: str = "GC Metrics"

# Name of the synthetic dur=0 instant event emitted on the process track
# itself, once per pid, on the first non-meta event for that pid. The
# Perfetto UI hides a track that has zero events, which would hide the
# process track's `description` (the joined cmdline). Dropping this
# marker guarantees the process track has at least one event so the
# description is always visible — regardless of whether the caller
# emitted any `InstantEvent` for the pid.
_START_PROCESS_INSTANT_NAME: str = "Start Process"


def _emit_root_descriptor(
    state: PerfettoTrackState,
    sequence_id: int,
) -> list[bytes]:
    """Build the special root ``TrackDescriptor`` (``uuid = 0``) that
    carries the ``process_ordering`` and ``thread_ordering`` hints.

    The root descriptor is the magic uuid-0 track that tells the
    Perfetto UI to honor ``sibling_order_rank`` on top-level process /
    thread tracks. It is emitted exactly once per trace, guarded by
    ``state.has_root_descriptor``. The descriptor has no ``name`` and
    no ``process`` / ``thread`` / ``counter`` sub-message, so the
    trace processor does not surface it as a track row.

    NOTE: honoring ``process_ordering`` on the root descriptor requires
    Perfetto trace processor 0.57+ (not yet released as of this
    writing), and the corresponding UI feature is gated behind the
    "canary" channel in ``ui.perfetto.dev`` (Flags → Release channel -> Canary).
    Older trace processors ignore the hints and fall back to their default
    ordering. We always emit the hints regardless so traces are
    forward-compatible — no version gate is applied at write time.
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

    When *sibling_order_rank* is not ``None`` it is written into the
    descriptor's ``sibling_order_rank`` field so the Perfetto UI can
    order this process track relative to siblings (only honored when
    the root track descriptor carries
    ``process_ordering = PROCESS_ORDERING_EXPLICIT``; see
    ``_emit_root_descriptor``).

    When *start_timestamp_ns* is not ``None`` it is written into the
    ``process`` sub-message's ``start_timestamp_ns`` field, per the
    Perfetto proto at
    ``protos/perfetto/trace/track_event/process_descriptor.proto`` (field
    7). The value is the first non-meta event timestamp for *pid* in
    nanoseconds (the trace's native time unit), which is the same
    value used to derive ``sibling_order_rank``.
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

    Idempotent per pid. A Perfetto track with zero events is hidden in
    the UI, which would hide the process track's ``description`` (the
    joined cmdline) when the caller did not emit any ``InstantEvent``
    for the pid. Dropping this marker guarantees the process track has
    at least one event, so the description is always visible. Emitted
    lazily on the first non-meta event for the pid, using that event's
    timestamp.
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


def _emit_counter_group_descriptor(
    pid: int,
    iid: int,
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[int, list[bytes]]:
    """Build the per-(pid, iid) GC Metrics grouping track descriptor.

    The group is a plain custom track (no ``process``/``thread`` field) so
    Perfetto honors ``child_ordering``/``sibling_order_rank`` on its children
    — unlike OS-scoped process/thread tracks where those fields are ignored.
    Counter tracks are parented to this group; the group itself is parented
    to the process track.
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
    name: str,
    metric: str,
    state: PerfettoTrackState,
    sequence_id: int,
    display_name: str | None = None,
) -> tuple[int, list[bytes]]:
    """Build a counter track descriptor if not already emitted.

    Metrics in ``_TOPLEVEL_COUNTER_METRICS`` are parented directly to the
    process track (outside the GC Metrics group) so they render at the top
    level. All other counters are parented to the per-(pid, iid) GC Metrics
    group track so that ``sibling_order_rank`` from ``_COUNTER_RANKS`` is
    honored by trace processor and the UI.

    When ``display_name`` is provided, it is used as the on-the-wire track
    name; otherwise the default ``f"{name} {metric}"`` form is used.
    """
    if metric in _TOPLEVEL_COUNTER_METRICS:
        if state.has_counter_track(pid, iid, name, metric):
            return state.get_or_create_counter_track_uuid(pid, iid, name, metric), []
        ctr_uuid = state.get_or_create_counter_track_uuid(pid, iid, name, metric)
        track_name = display_name if display_name is not None else f"{name} {metric}"
        desc = build_track_descriptor(
            ctr_uuid,
            track_name,
            parent_uuid=state.get_process_track_uuid(pid),
            is_counter=True,
            sibling_order_rank=_COUNTER_RANKS.get(metric, 0),
        )
        return ctr_uuid, [build_trace_packet(sequence_id, track_descriptor=desc)]
    group_uuid, group_packets = _emit_counter_group_descriptor(pid, iid, state, sequence_id)
    if state.has_counter_track(pid, iid, name, metric):
        ctr_uuid = state.get_or_create_counter_track_uuid(pid, iid, name, metric)
        return ctr_uuid, group_packets
    ctr_uuid = state.get_or_create_counter_track_uuid(pid, iid, name, metric)
    track_name = display_name if display_name is not None else f"{name} {metric}"
    desc = build_track_descriptor(
        ctr_uuid,
        track_name,
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

    The caller MUST include ``ProcessMeta`` / ``ThreadMeta`` events in the
    list (at least once per pid / tid) for track descriptors to be emitted.
    The ``PerfettoExporter`` does this automatically.

    Returns ``(descriptors, packets)``, each element being a list of encoded
    ``TracePacket`` bytes ready to be wrapped by ``build_trace``.

    On the first call for a given ``state`` this emits the root
    ``TrackDescriptor`` (``uuid = 0``), which is what tells the Perfetto
    UI to honor ``sibling_order_rank`` on process and thread tracks.

    Each process descriptor carries a ``sibling_order_rank`` and a
    ``process.start_timestamp_ns``, both derived from the pid's first
    non-meta event. ``state`` accumulates that across batches; the
    pre-pass below folds the current batch in *before* the main loop, so
    a pid whose first event shares a batch with its ``ProcessMeta``
    still gets a rank. Pids with no recorded span get neither field.

    ``Processes``-track slices are not emitted here — both ends go out
    at trace close via ``finalize_perfetto_packets``.
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

        # The exporter is expected to emit ProcessMeta before any
        # ThreadMeta for a given pid.
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
            single_arg = len(event.args) == 1
            for metric, value in event.args.items():
                display_name = metric if single_arg else f"{event.name} {metric}"
                ctr_uuid, desc_bytes = _emit_counter_track_descriptor(
                    pid,
                    event.tid,
                    event.name,
                    metric,
                    state,
                    sequence_id,
                    display_name=display_name,
                )
                descriptors.extend(desc_bytes)
                packets.append(
                    build_trace_packet(
                        sequence_id,
                        timestamp=event.ts,
                        track_event=_make_counter_event(ctr_uuid, value),
                    )
                )

    return descriptors, packets


def _maybe_emit_start_process_marker(
    event: TraceEvent,
    state: PerfettoTrackState,
    sequence_id: int,
    packets: list[bytes],
) -> None:
    """Emit the ``Start Process`` marker once per pid, on the first non-meta
    event for that pid, using that event's timestamp."""
    if not state.has_pid(event.pid) or state.has_start_process_marker(event.pid):
        return
    ts = getattr(event, "ts", 0)
    packets.extend(_emit_start_process_marker(event.pid, ts, state, sequence_id))

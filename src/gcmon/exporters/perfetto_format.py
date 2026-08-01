"""Perfetto protobuf message builders and GC-to-Perfetto conversion.

The protobuf building primitives (build_track_descriptor, build_trace_packet,
build_track_event, etc.) remain here along with PerfettoTrackState.

The function ``convert_trace_events_to_perfetto`` accepts a list of ``TraceEvent``
objects (produced by the shared ``trace_converter``) and maps each to
the corresponding Perfetto protobuf representation.
"""

from collections.abc import Sequence
from enum import IntEnum

from ..trace_event import (
    BeginEvent,
    CounterEvent,
    EndEvent,
    InstantEvent,
    ProcessMeta,
    ThreadMeta,
    TraceEvent,
)
from .protobuf_encoder import (
    encode_bytes_field,
    encode_double_field,
    encode_string_field,
    encode_varint_field,
)


class TraceField(IntEnum):
    PACKET = 1


class TracePacketField(IntEnum):
    TIMESTAMP = 8
    SEQUENCE_ID = 10
    TRACK_EVENT = 11
    TRACK_DESCRIPTOR = 60


class TrackDescriptorField(IntEnum):
    UUID = 1
    NAME = 2
    PROCESS = 3
    THREAD = 4
    PARENT_UUID = 5
    COUNTER = 8
    CHILD_ORDERING = 11
    SIBLING_ORDER_RANK = 12
    DESCRIPTION = 14
    PROCESS_ORDERING = 19
    THREAD_ORDERING = 20


class ChildTracksOrdering(IntEnum):
    UNKNOWN = 0
    LEXICOGRAPHIC = 1
    CHRONOLOGICAL = 2
    EXPLICIT = 3


class ProcessOrdering(IntEnum):
    UNSPECIFIED = 0
    EXPLICIT = 1


class ThreadOrdering(IntEnum):
    UNSPECIFIED = 0
    EXPLICIT = 1


class ThreadDescriptorField(IntEnum):
    PID = 1
    TID = 2
    THREAD_NAME = 5


class ProcessDescriptorField(IntEnum):
    PID = 1
    CMDLINE = 2
    PROCESS_NAME = 6
    START_TIMESTAMP_NS = 7


class CounterDescriptorField(IntEnum):
    TYPE = 1
    CATEGORIES = 2
    UNIT = 3
    UNIT_MULTIPLIER = 4
    IS_INCREMENTAL = 5
    UNIT_NAME = 6
    Y_AXIS_SHARE_KEY = 7


class TrackEventField(IntEnum):
    TYPE = 9
    TRACK_UUID = 11
    DEBUG_ANNOTATIONS = 4
    CATEGORIES = 22
    NAME = 23
    COUNTER_VALUE = 30
    DOUBLE_COUNTER_VALUE = 44
    TIMESTAMP_DELTA_US = 1
    TIMESTAMP_ABSOLUTE_US = 16


class DebugAnnotationField(IntEnum):
    # NAME lives at field 10, not field 1. The proto defines
    # `oneof name_field { uint64 name_iid = 1; string name = 10; }`;
    # only one variant of a oneof may be set, so we must use the current
    # `name` slot. Field 1 is now an interned IID (uint64); writing a
    # string there would be misinterpreted as a garbage IID. Do not
    # "fix" this back to 1.
    NAME = 10
    BOOL_VALUE = 2
    INT_VALUE = 4
    STRING_VALUE = 6


class TrackEventType(IntEnum):
    SLICE_BEGIN = 1
    SLICE_END = 2
    INSTANT = 3
    COUNTER = 4


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

# Name of the shared top-level Perfetto track that shows one
# TYPE_SLICE_BEGIN / TYPE_SLICE_END pair per pid, spanning that pid's
# first non-meta event to its last non-counter one. Because every pid
# shares this track, and slices on a Perfetto track have to nest, spans
# that overlap without nesting are clipped by
# `finalize_perfetto_packets`; see ADR-0011.
_PROCESS_LIFETIME_TRACK_NAME: str = "Processes"


class PerfettoTrackState:
    def __init__(self) -> None:
        self._pids: set[int] = set()
        self._tids: set[tuple[int, int]] = set()
        self._cmdlines: dict[int, list[str]] = {}
        self._counter_tracks: dict[tuple[int, int, str, str], int] = {}
        self._counter_group_uuids: dict[tuple[int, int], int] = {}
        self._pid_uuids: dict[int, int] = {}
        self._tid_uuids: dict[tuple[int, int], int] = {}
        self._start_process_marker_emitted: set[int] = set()
        self._process_lifetime_track_uuid: int | None = None
        self._process_lifetime_start: dict[int, int] = {}
        self._process_lifetime_end: dict[int, int] = {}
        self._process_lifetime_drained: bool = False
        self._root_descriptor_emitted: bool = False
        self._next_uuid: int = 1

    def _alloc_uuid(self) -> int:
        uuid = self._next_uuid
        self._next_uuid += 1
        return uuid

    def has_pid(self, pid: int) -> bool:
        return pid in self._pids

    def mark_pid(self, pid: int) -> None:
        self._pids.add(pid)

    def has_tid(self, pid: int, iid: int) -> bool:
        return (pid, iid) in self._tids

    def mark_tid(self, pid: int, iid: int) -> None:
        self._tids.add((pid, iid))

    def set_cmdline(self, pid: int, cmdline: list[str]) -> None:
        self._cmdlines[pid] = cmdline

    def get_cmdline(self, pid: int) -> list[str] | None:
        return self._cmdlines.get(pid)

    def get_process_track_uuid(self, pid: int) -> int:
        if pid not in self._pid_uuids:
            self._pid_uuids[pid] = self._alloc_uuid()
        return self._pid_uuids[pid]

    def get_thread_track_uuid(self, pid: int, iid: int) -> int:
        key = (pid, iid)
        if key not in self._tid_uuids:
            self._tid_uuids[key] = self._alloc_uuid()
        return self._tid_uuids[key]

    def has_counter_track(self, pid: int, iid: int, name: str, metric: str) -> bool:
        return (pid, iid, name, metric) in self._counter_tracks

    def get_or_create_counter_track_uuid(self, pid: int, iid: int, name: str, metric: str) -> int:
        key = (pid, iid, name, metric)
        if key not in self._counter_tracks:
            self._counter_tracks[key] = self._alloc_uuid()
        return self._counter_tracks[key]

    def has_counter_group_track(self, pid: int, iid: int) -> bool:
        return (pid, iid) in self._counter_group_uuids

    def get_or_create_counter_group_track_uuid(self, pid: int, iid: int) -> int:
        key = (pid, iid)
        if key not in self._counter_group_uuids:
            self._counter_group_uuids[key] = self._alloc_uuid()
        return self._counter_group_uuids[key]

    def has_start_process_marker(self, pid: int) -> bool:
        return pid in self._start_process_marker_emitted

    def mark_start_process_marker(self, pid: int) -> None:
        self._start_process_marker_emitted.add(pid)

    def has_process_lifetime_track(self) -> bool:
        return self._process_lifetime_track_uuid is not None

    def get_or_create_process_lifetime_track_uuid(self) -> int:
        if self._process_lifetime_track_uuid is None:
            self._process_lifetime_track_uuid = self._alloc_uuid()
        return self._process_lifetime_track_uuid

    def has_process_lifetime(self, pid: int) -> bool:
        return pid in self._process_lifetime_start

    def update_process_lifetime(self, pid: int, ts: int, *, extends_end: bool) -> None:
        """Fold *ts* into the recorded span for *pid*.

        The start is a minimum over every non-meta event; the end is a
        maximum over non-counter events only, so *extends_end* must be
        ``False`` for a ``CounterEvent``. The two are held separately so
        a counter can never seed the end, not even as the first event
        folded for a pid; a counter-only pid therefore keeps its rank but
        gets no slice. ADR-0011 has the rationale, and why the asymmetry
        is provisional.
        """
        start_ts = self._process_lifetime_start.get(pid)
        if start_ts is None or ts < start_ts:
            self._process_lifetime_start[pid] = ts
        if not extends_end:
            return
        end_ts = self._process_lifetime_end.get(pid)
        if end_ts is None or ts > end_ts:
            self._process_lifetime_end[pid] = ts

    def get_process_lifetime_start_ts(self, pid: int) -> int | None:
        return self._process_lifetime_start.get(pid)

    def pop_process_lifetimes(self) -> list[tuple[int, int, int]]:
        """Return ``[(pid, start_ts, end_ts), ...]`` for every pid with
        both a start and an end, sorted by ``(start_ts, -end_ts, pid)`` --
        the order ``_clip_spans_to_laminar`` requires. A pid seen only
        through counters has no end and is absent.

        Drains: a second call returns an empty list, which is what makes
        ``finalize_perfetto_packets`` safe to call twice. The spans
        themselves are kept, so the query methods above keep working.
        """
        if self._process_lifetime_drained:
            return []
        self._process_lifetime_drained = True
        return sorted(
            ((pid, self._process_lifetime_start[pid], end) for pid, end in self._process_lifetime_end.items()),
            key=lambda item: (item[1], -item[2], item[0]),
        )

    def get_process_track_ranks(self) -> dict[int, int]:
        """Return ``{pid: rank}``, assigned sequentially from ``0`` by
        ascending ``(start_ts, pid)``. Pids with no recorded start are
        absent."""
        if not self._process_lifetime_start:
            return {}
        sorted_pids = sorted(
            self._process_lifetime_start.keys(),
            key=lambda p: (self._process_lifetime_start[p], p),
        )
        return {pid: rank for rank, pid in enumerate(sorted_pids)}

    def has_root_descriptor(self) -> bool:
        return self._root_descriptor_emitted

    def mark_root_descriptor_emitted(self) -> None:
        self._root_descriptor_emitted = True


def build_track_descriptor(
    uuid: int,
    name: str,
    pid: int | None = None,
    tid: int | None = None,
    parent_uuid: int | None = None,
    is_counter: bool = False,
    child_ordering: ChildTracksOrdering | None = None,
    sibling_order_rank: int | None = None,
    thread_name: str | None = None,
    cmdline: list[str] | None = None,
    description: str | None = None,
    process_ordering: ProcessOrdering | None = None,
    thread_ordering: ThreadOrdering | None = None,
    start_timestamp_ns: int | None = None,
    y_axis_share_key: str | None = None,
) -> bytes:
    """Build a ``TrackDescriptor`` submessage as wire-format bytes.

    Parameters
    ----------
    uuid
        Track UUID; the special value ``0`` is reserved for the root
        track descriptor that carries ``process_ordering`` /
        ``thread_ordering`` hints.
    name
        Human-readable track name. Falsy values (empty string) suppress
        the ``name`` field — used for the root descriptor and for any
        other track where the name would be redundant.
    pid, tid, thread_name, cmdline, start_timestamp_ns
        Populate the OS-association sub-message: ``ThreadDescriptor`` if
        both ``pid`` and ``tid`` are given, else ``ProcessDescriptor``.
    parent_uuid
        Optional parent track UUID; sets ``TrackDescriptor.parent_uuid``.
    is_counter
        When ``True``, emits an empty ``CounterDescriptor`` sub-message
        (or a populated one if ``y_axis_share_key`` is set) at
        ``TrackDescriptor.counter`` (field 8).
    child_ordering, sibling_order_rank
        Grouped-track ordering hints consumed by trace processor.
    description
        Human-readable description; surfaced in the Perfetto UI as a
        tooltip on the track's help icon.
    process_ordering, thread_ordering
        Root-descriptor-only hints that tell the UI to honor
        ``sibling_order_rank`` on process / thread tracks. Only set
        these on the root descriptor (``uuid = 0``).
    y_axis_share_key
        Optional string used to group counter tracks with the same
        parent on a shared Y-axis in the Perfetto UI. Only effective
        when ``is_counter=True`` and the value is a non-empty string;
        an empty string is treated as "no key set" (REQ-6 in
        ``specs/17 - counter-y-axis-sharing.md``). Ignored entirely
        when ``is_counter=False``. See
        https://perfetto.dev/docs/reference/synthetic-track-event#sharing-y-axis-between-counters
    """
    result = encode_varint_field(TrackDescriptorField.UUID, uuid)
    if name:
        result += encode_string_field(TrackDescriptorField.NAME, name)
    if pid is not None and tid is not None:
        thread_desc = encode_varint_field(ThreadDescriptorField.PID, pid) + encode_varint_field(
            ThreadDescriptorField.TID, tid
        )
        if thread_name is not None:
            thread_desc += encode_string_field(ThreadDescriptorField.THREAD_NAME, thread_name)
        result += encode_bytes_field(TrackDescriptorField.THREAD, thread_desc)
    elif pid is not None:
        process_desc = encode_varint_field(ProcessDescriptorField.PID, pid)
        if cmdline:
            for arg in cmdline:
                process_desc += encode_string_field(ProcessDescriptorField.CMDLINE, arg)
        process_desc += encode_string_field(ProcessDescriptorField.PROCESS_NAME, name)
        if start_timestamp_ns is not None:
            process_desc += encode_varint_field(
                ProcessDescriptorField.START_TIMESTAMP_NS,
                start_timestamp_ns,
            )
        result += encode_bytes_field(TrackDescriptorField.PROCESS, process_desc)
    if parent_uuid is not None:
        result += encode_varint_field(TrackDescriptorField.PARENT_UUID, parent_uuid)
    if thread_ordering is not None:
        result += encode_varint_field(TrackDescriptorField.THREAD_ORDERING, thread_ordering)
    if process_ordering is not None:
        result += encode_varint_field(TrackDescriptorField.PROCESS_ORDERING, process_ordering)
    if is_counter:
        if y_axis_share_key:
            counter_desc = encode_string_field(
                CounterDescriptorField.Y_AXIS_SHARE_KEY,
                y_axis_share_key,
            )
            result += encode_bytes_field(TrackDescriptorField.COUNTER, counter_desc)
        else:
            result += encode_bytes_field(TrackDescriptorField.COUNTER, b"")
    if child_ordering is not None:
        result += encode_varint_field(TrackDescriptorField.CHILD_ORDERING, child_ordering)
    if sibling_order_rank is not None:
        result += encode_varint_field(TrackDescriptorField.SIBLING_ORDER_RANK, sibling_order_rank)
    if description is not None:
        result += encode_string_field(TrackDescriptorField.DESCRIPTION, description)
    return result


def build_trace_packet(
    sequence_id: int,
    timestamp: int | None = None,
    track_event: bytes | None = None,
    track_descriptor: bytes | None = None,
) -> bytes:
    result = b""
    if timestamp is not None:
        result += encode_varint_field(TracePacketField.TIMESTAMP, timestamp)
    result += encode_varint_field(TracePacketField.SEQUENCE_ID, sequence_id)
    if track_event is not None:
        result += encode_bytes_field(TracePacketField.TRACK_EVENT, track_event)
    if track_descriptor is not None:
        result += encode_bytes_field(TracePacketField.TRACK_DESCRIPTOR, track_descriptor)
    return result


def _build_debug_annotation_int(name: str, value: int) -> bytes:
    result = encode_string_field(DebugAnnotationField.NAME, name)
    result += encode_varint_field(DebugAnnotationField.INT_VALUE, value)
    return result


def _build_debug_annotation_string(name: str, value: str) -> bytes:
    result = encode_string_field(DebugAnnotationField.NAME, name)
    result += encode_string_field(DebugAnnotationField.STRING_VALUE, value)
    return result


def build_track_event(
    type: TrackEventType,
    track_uuid: int,
    name: str | None = None,
    categories: list[str] | None = None,
    counter_value: int | None = None,
    double_counter_value: float | None = None,
    debug_annotations: list[bytes] | None = None,
) -> bytes:
    result = encode_varint_field(TrackEventField.TYPE, type)
    result += encode_varint_field(TrackEventField.TRACK_UUID, track_uuid)
    if categories:
        for cat in categories:
            result += encode_string_field(TrackEventField.CATEGORIES, cat)
    if name is not None:
        result += encode_string_field(TrackEventField.NAME, name)
    if counter_value is not None:
        result += encode_varint_field(TrackEventField.COUNTER_VALUE, counter_value)
    if double_counter_value is not None:
        result += encode_double_field(
            TrackEventField.DOUBLE_COUNTER_VALUE,
            double_counter_value,
        )
    if debug_annotations:
        for ann in debug_annotations:
            result += encode_bytes_field(TrackEventField.DEBUG_ANNOTATIONS, ann)
    return result


def build_trace(packets: list[bytes]) -> bytes:
    result = b""
    for packet in packets:
        result += encode_bytes_field(TraceField.PACKET, packet)
    return result


def _make_slice_begin(
    track_uuid: int,
    name: str,
    categories: list[str],
    annotations: list[bytes],
) -> bytes:
    return build_track_event(
        type=TrackEventType.SLICE_BEGIN,
        track_uuid=track_uuid,
        name=name,
        categories=categories,
        debug_annotations=annotations,
    )


def _make_slice_end(track_uuid: int) -> bytes:
    return build_track_event(
        type=TrackEventType.SLICE_END,
        track_uuid=track_uuid,
    )


def _make_counter_event(track_uuid: int, value: int | float) -> bytes:
    if isinstance(value, float):
        return build_track_event(
            type=TrackEventType.COUNTER,
            track_uuid=track_uuid,
            double_counter_value=value,
        )
    return build_track_event(
        type=TrackEventType.COUNTER,
        track_uuid=track_uuid,
        counter_value=value,
    )


def _args_to_debug_annotations(args: dict[str, int]) -> list[bytes]:
    return [_build_debug_annotation_int(k, v) for k, v in args.items()]


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

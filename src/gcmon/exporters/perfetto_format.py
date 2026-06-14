"""Perfetto protobuf message builders and GC-to-Perfetto conversion.

The protobuf building primitives (build_track_descriptor, build_trace_packet,
build_track_event, etc.) remain here along with PerfettoTrackState.

The function ``convert_trace_events_to_perfetto`` accepts a list of ``TraceEvent``
objects (produced by the shared ``trace_converter``) and maps each to
the corresponding Perfetto protobuf representation.
"""

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


class ChildTracksOrdering(IntEnum):
    UNKNOWN = 0
    LEXICOGRAPHIC = 1
    CHRONOLOGICAL = 2
    EXPLICIT = 3


class ThreadDescriptorField(IntEnum):
    PID = 1
    TID = 2
    THREAD_NAME = 5


class ProcessDescriptorField(IntEnum):
    PID = 1
    CMDLINE = 2
    PROCESS_NAME = 6


class TrackEventField(IntEnum):
    TYPE = 9
    TRACK_UUID = 11
    DEBUG_ANNOTATIONS = 4
    CATEGORIES = 22
    NAME = 23
    COUNTER_VALUE = 30
    TIMESTAMP_DELTA_US = 1
    TIMESTAMP_ABSOLUTE_US = 16


class DebugAnnotationField(IntEnum):
    NAME = 1
    BOOL_VALUE = 2
    INT_VALUE = 4
    STRING_VALUE = 6


__all__ = [
    "TYPE_COUNTER",
    "TYPE_INSTANT",
    "TYPE_SLICE_BEGIN",
    "TYPE_SLICE_END",
    "ChildTracksOrdering",
    "DebugAnnotationField",
    "PerfettoTrackState",
    "ProcessDescriptorField",
    "ThreadDescriptorField",
    "TraceField",
    "TracePacketField",
    "TrackDescriptorField",
    "TrackEventField",
    "build_trace",
    "build_trace_packet",
    "build_track_descriptor",
    "build_track_event",
    "convert_trace_events_to_perfetto",
]

TYPE_SLICE_BEGIN = 1
TYPE_SLICE_END = 2
TYPE_INSTANT = 3
TYPE_COUNTER = 4

_PROCESS_SHIFT = 60
_THREAD_SHIFT = 60
_PROCESS_BASE = 1 << _PROCESS_SHIFT
_THREAD_BASE = 1 << _THREAD_SHIFT
_COUNTER_BASE = 3 << 60

_COUNTER_RANKS: dict[str, int] = {
    "collected": 1,
    "uncollectable": 2,
    "candidates": 3,
    "heap_size": 4,
    "increment_size": 5,
    "alive_size": 6,
    "finalized_garbage_count": 7,
    "deleted_garbage_count": 8,
    "clear_weakrefs_count": 9,
}


class PerfettoTrackState:
    def __init__(self) -> None:
        self._pids: set[int] = set()
        self._tids: set[tuple[int, int]] = set()
        self._cmdlines: dict[int, list[str]] = {}
        self._counter_tracks: dict[tuple[int, int, int, str], int] = {}
        self._counter_counter = 0

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
        return pid | _PROCESS_BASE

    def get_thread_track_uuid(self, pid: int, iid: int) -> int:
        return (pid << 20) | iid | _THREAD_BASE

    def has_counter_track(self, pid: int, iid: int, gen: int, metric: str) -> bool:
        return (pid, iid, gen, metric) in self._counter_tracks

    def get_or_create_counter_track_uuid(self, pid: int, iid: int, gen: int, metric: str) -> int:
        key = (pid, iid, gen, metric)
        if key not in self._counter_tracks:
            self._counter_tracks[key] = _COUNTER_BASE + self._counter_counter
            self._counter_counter += 1
        return self._counter_tracks[key]


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
) -> bytes:
    result = encode_varint_field(TrackDescriptorField.UUID, uuid)
    result += encode_string_field(TrackDescriptorField.NAME, name)
    if pid is not None and tid is not None:
        thread_desc = encode_varint_field(ThreadDescriptorField.PID, pid) + encode_varint_field(ThreadDescriptorField.TID, tid)
        if thread_name is not None:
            thread_desc += encode_string_field(ThreadDescriptorField.THREAD_NAME, thread_name)
        result += encode_bytes_field(TrackDescriptorField.THREAD, thread_desc)
    elif pid is not None:
        process_desc = encode_varint_field(ProcessDescriptorField.PID, pid)
        if cmdline:
            for arg in cmdline:
                process_desc += encode_string_field(ProcessDescriptorField.CMDLINE, arg)
        process_desc += encode_string_field(ProcessDescriptorField.PROCESS_NAME, name)
        result += encode_bytes_field(TrackDescriptorField.PROCESS, process_desc)
    if parent_uuid is not None:
        result += encode_varint_field(TrackDescriptorField.PARENT_UUID, parent_uuid)
    if is_counter:
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


def build_track_event(
    type: int,
    track_uuid: int,
    name: str | None = None,
    categories: list[str] | None = None,
    counter_value: int | None = None,
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
        type=TYPE_SLICE_BEGIN,
        track_uuid=track_uuid,
        name=name,
        categories=categories,
        debug_annotations=annotations,
    )


def _make_slice_end(track_uuid: int) -> bytes:
    return build_track_event(
        type=TYPE_SLICE_END,
        track_uuid=track_uuid,
    )


def _make_counter_event(track_uuid: int, value: int) -> bytes:
    return build_track_event(
        type=TYPE_COUNTER,
        track_uuid=track_uuid,
        counter_value=value,
    )


def _args_to_debug_annotations(args: dict[str, int]) -> list[bytes]:
    return [_build_debug_annotation_int(k, v) for k, v in args.items()]


def _emit_process_descriptor(
    pid: int,
    state: PerfettoTrackState,
    sequence_id: int,
) -> list[bytes]:
    """Build a process track descriptor if not already emitted for *pid*."""
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
        cmdline=cmdline,
        description=" ".join(cmdline) if cmdline else None,
    )
    return [build_trace_packet(sequence_id, track_descriptor=desc)]


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


def _emit_counter_track_descriptor(
    pid: int,
    iid: int,
    gen: int,
    metric: str,
    state: PerfettoTrackState,
    sequence_id: int,
) -> list[bytes]:
    """Build a counter track descriptor if not already emitted for this metric track."""
    if state.has_counter_track(pid, iid, gen, metric):
        return []
    ctr_uuid = state.get_or_create_counter_track_uuid(pid, iid, gen, metric)
    desc = build_track_descriptor(
        ctr_uuid,
        f"{metric} (gen={gen})",
        parent_uuid=state.get_process_track_uuid(pid),
        is_counter=True,
        sibling_order_rank=_COUNTER_RANKS.get(metric, 0),
    )
    return [build_trace_packet(sequence_id, track_descriptor=desc)]


def convert_trace_events_to_perfetto(
    events: list[TraceEvent],
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[list[bytes], list[bytes]]:
    """Convert a list of ``TraceEvent`` objects to Perfetto protobuf packets.

    The caller MUST include ``ProcessMeta`` / ``ThreadMeta`` events in the
    list (at least once per pid / tid) for track descriptors to be emitted.
    The ``PerfettoExporter`` does this automatically.

    Returns ``(descriptors, packets)``, each element being a list of encoded
    ``TracePacket`` bytes ready to be wrapped by ``build_trace``.
    """
    descriptors: list[bytes] = []
    packets: list[bytes] = []

    for event in events:
        pid = event.pid

        if isinstance(event, ProcessMeta):
            descriptors.extend(_emit_process_descriptor(pid, state, sequence_id))

        # The exporter is expected to emit ProcessMeta before any
        # ThreadMeta for a given pid.
        elif isinstance(event, ThreadMeta):
            descriptors.extend(_emit_process_descriptor(pid, state, sequence_id))
            descriptors.extend(_emit_thread_descriptor(pid, event.tid, state, sequence_id))

        elif isinstance(event, BeginEvent):
            thread_uuid = state.get_thread_track_uuid(pid, event.tid)
            annotations = _args_to_debug_annotations(event.args)
            packets.append(build_trace_packet(
                sequence_id, timestamp=event.ts,
                track_event=_make_slice_begin(
                    thread_uuid, event.name, [event.cat],
                    annotations,
                ),
            ))

        elif isinstance(event, EndEvent):
            thread_uuid = state.get_thread_track_uuid(pid, event.tid)
            packets.append(build_trace_packet(
                sequence_id, timestamp=event.ts,
                track_event=_make_slice_end(thread_uuid),
            ))

        elif isinstance(event, InstantEvent):
            proc_uuid = state.get_process_track_uuid(pid)
            packets.append(build_trace_packet(
                sequence_id, timestamp=event.ts,
                track_event=build_track_event(
                    type=TYPE_INSTANT,
                    track_uuid=proc_uuid,
                    name=event.name,
                ),
            ))

        elif isinstance(event, CounterEvent):
            gen = int(event.name[1:])  # name format: "G{gen}"
            for metric, value in event.args.items():
                descriptors.extend(_emit_counter_track_descriptor(pid, event.tid, gen, metric, state, sequence_id))
                ctr_uuid = state.get_or_create_counter_track_uuid(pid, event.tid, gen, metric)
                packets.append(build_trace_packet(
                    sequence_id, timestamp=event.ts,
                    track_event=_make_counter_event(ctr_uuid, value),
                ))

    return descriptors, packets

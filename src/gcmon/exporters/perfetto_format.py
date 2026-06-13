"""Perfetto protobuf message builders and IR-to-Perfetto serializer.

The format-neutral event representation lives in :mod:`gcmon.exporters.ir`.
This module exposes the protobuf field encoders, the :class:`PerfettoTrackState`
used to deduplicate track descriptors, and :func:`ir_to_perfetto` which
serializes a list of IR events to Perfetto packets.

The legacy :func:`convert_item_to_perfetto_packets` and
:func:`convert_instant_to_perfetto_packet` are retained as shims that route
through the IR and the serializer.
"""

from enum import IntEnum
from typing import cast

from ..data import ts_to_us
from ..protocol import TGCStatsInfo, TInstantMsg
from .ir import (
    IRCounterEvent,
    IRIncrementalEvent,
    IRInstantEvent,
    IRPauseEvent,
    convert_instant_to_ir,
    convert_item_to_ir,
)
from .ir import (
    TraceEvent as IRTraceEvent,
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
    "convert_instant_to_perfetto_packet",
    "convert_item_to_perfetto_packets",
    "ir_to_perfetto",
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


def _emit_process_descriptor(
    pid: int,
    ts_us: int,
    sequence_id: int,
    state: PerfettoTrackState,
    descriptors: list[bytes],
) -> int:
    """Emit a process track descriptor on first sighting. Returns the proc uuid."""
    proc_uuid = state.get_process_track_uuid(pid)
    if state.has_pid(pid):
        return proc_uuid
    state.mark_pid(pid)
    cmdline = state.get_cmdline(pid)
    desc = build_track_descriptor(
        proc_uuid,
        f"Process {pid}",
        pid=pid,
        child_ordering=ChildTracksOrdering.EXPLICIT,
        cmdline=cmdline,
        description=" ".join(cmdline) if cmdline else None,
    )
    descriptors.append(build_trace_packet(sequence_id, timestamp=ts_us, track_descriptor=desc))
    return proc_uuid


def _emit_thread_descriptor(
    pid: int,
    iid: int,
    proc_uuid: int,
    ts_us: int,
    sequence_id: int,
    state: PerfettoTrackState,
    descriptors: list[bytes],
) -> int:
    """Emit a thread track descriptor on first sighting. Returns the thread uuid."""
    thread_uuid = state.get_thread_track_uuid(pid, iid)
    if state.has_tid(pid, iid):
        return thread_uuid
    state.mark_tid(pid, iid)
    desc = build_track_descriptor(
        thread_uuid,
        f"Thread {iid}",
        pid=pid,
        tid=pid if iid == 0 else iid,
        parent_uuid=proc_uuid,
        sibling_order_rank=0,
        thread_name=f"Thread {iid}",
    )
    descriptors.append(build_trace_packet(sequence_id, timestamp=ts_us, track_descriptor=desc))
    return thread_uuid


def _annotations_from_args(args: object) -> list[bytes]:
    """Convert an IR ``args`` dict to a list of perfetto debug-annotation bytes."""
    annotations: list[bytes] = []
    if not isinstance(args, dict):
        return annotations
    for name, value in args.items():
        if isinstance(value, int) and not isinstance(value, bool):
            annotations.append(_build_debug_annotation_int(name, value))
    return annotations


def _emit_pause_or_incremental(
    event: IRPauseEvent | IRIncrementalEvent,
    proc_uuid: int,
    thread_uuid: int,
    sequence_id: int,
    packets: list[bytes],
) -> None:
    cat = [event["cat"]]
    annotations = _annotations_from_args(event["args"])
    ts_start_us = ts_to_us(event["ts_start_ns"])
    ts_stop_us = ts_to_us(event["ts_start_ns"] + int(event["dur_ns"]))
    packets.append(build_trace_packet(
        sequence_id, timestamp=ts_start_us,
        track_event=_make_slice_begin(thread_uuid, event["name"], cat, annotations),
    ))
    packets.append(build_trace_packet(
        sequence_id, timestamp=ts_stop_us,
        track_event=_make_slice_end(thread_uuid),
    ))


def _emit_counter_event(
    event: IRCounterEvent,
    sequence_id: int,
    state: PerfettoTrackState,
    descriptors: list[bytes],
    packets: list[bytes],
) -> None:
    pid = event["pid"]
    iid = event["tid"]
    gen = event["gen"]
    proc_uuid = state.get_process_track_uuid(pid)
    ts_us = ts_to_us(event["ts_ns"])
    for metric, value in event["args"].items():
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        is_new = not state.has_counter_track(pid, iid, gen, metric)
        ctr_uuid = state.get_or_create_counter_track_uuid(pid, iid, gen, metric)
        if is_new:
            desc = build_track_descriptor(
                ctr_uuid,
                f"{metric} (gen={gen})",
                parent_uuid=proc_uuid,
                is_counter=True,
                sibling_order_rank=_COUNTER_RANKS.get(metric, 0),
            )
            descriptors.append(build_trace_packet(sequence_id, track_descriptor=desc))
        packets.append(build_trace_packet(
            sequence_id, timestamp=ts_us,
            track_event=_make_counter_event(ctr_uuid, value),
        ))


def _emit_instant(
    event: IRInstantEvent,
    sequence_id: int,
    state: PerfettoTrackState,
    descriptors: list[bytes],
    packets: list[bytes],
) -> None:
    ts_us = ts_to_us(event["ts_ns"])
    proc_uuid = _emit_process_descriptor(event["pid"], ts_us, sequence_id, state, descriptors)
    packets.append(build_trace_packet(
        sequence_id, timestamp=ts_us,
        track_event=build_track_event(
            type=TYPE_INSTANT,
            track_uuid=proc_uuid,
            name=event["name"],
        ),
    ))


def ir_to_perfetto(
    events: list[IRTraceEvent],
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[list[bytes], list[bytes]]:
    """Serialize a list of IR events to Perfetto packets.

    The IR is format-neutral; this serializer owns all Perfetto-specific
    concerns: track descriptor emission and dedup (via ``state``), slice
    begin/end pairing, counter track fan-out, and timestamp unit conversion
    (nanoseconds → microseconds).
    """
    descriptors: list[bytes] = []
    packets: list[bytes] = []

    for event in events:
        if "ts_start_ns" in event:
            ts_us = ts_to_us(event["ts_start_ns"])  # type: ignore[typeddict-item]
            proc_uuid = _emit_process_descriptor(
                event["pid"], ts_us, sequence_id, state, descriptors
            )
            thread_uuid = _emit_thread_descriptor(
                event["pid"], event["tid"], proc_uuid, ts_us, sequence_id, state, descriptors  # type: ignore[typeddict-item]
            )
            _emit_pause_or_incremental(
                cast("IRPauseEvent | IRIncrementalEvent", event),
                proc_uuid, thread_uuid, sequence_id, packets,
            )
        elif "ts_ns" in event:
            if "tid" in event:
                _emit_counter_event(
                    cast(IRCounterEvent, event),
                    sequence_id, state, descriptors, packets,
                )
            else:
                _emit_instant(
                    cast(IRInstantEvent, event),
                    sequence_id, state, descriptors, packets,
                )

    return descriptors, packets


def convert_item_to_perfetto_packets(
    pid: int,
    item: TGCStatsInfo,
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[list[bytes], list[bytes]]:
    if item.ts_start >= item.ts_stop:
        ts_us = ts_to_us(item.ts_start)
        descriptors: list[bytes] = []
        proc_uuid = _emit_process_descriptor(
            pid, ts_us, sequence_id, state, descriptors
        )
        _emit_thread_descriptor(
            pid, item.iid, proc_uuid, ts_us, sequence_id, state, descriptors
        )
        return descriptors, []
    return ir_to_perfetto(convert_item_to_ir(pid, item), state, sequence_id)


def convert_instant_to_perfetto_packet(
    pid: int,
    item: TInstantMsg,
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[list[bytes], list[bytes]]:
    return ir_to_perfetto(
        convert_instant_to_ir(pid, item), state, sequence_id
    )

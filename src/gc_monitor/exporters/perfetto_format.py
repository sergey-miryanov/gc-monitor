"""Perfetto protobuf message builders and GC-to-Perfetto conversion."""

from enum import IntEnum

from ..data import ts_to_us
from ..protocol import (
    TGCStatsInfo,
    TInstantMsg,
    has_clear_weakrefs,
    has_deduce_unreachable,
    has_delete_garbage,
    has_finalize_garbage,
    has_handle_resurrected,
    has_handle_weakrefs,
    has_incremental,
    has_mark_alive,
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
    THREAD = 4
    PARENT_UUID = 5
    COUNTER = 8


class ThreadDescriptorField(IntEnum):
    PID = 1
    TID = 2


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
    "DebugAnnotationField",
    "PerfettoTrackState",
    "ThreadDescriptorField",
    "TraceField",
    "TracePacketField",
    "TrackDescriptorField",
    "TrackEventField",
    "build_trace",
    "build_trace_packet",
    "build_track_descriptor",
    "build_track_event",
    "convert_item_to_perfetto_packets",
]

TYPE_SLICE_BEGIN = 1
TYPE_SLICE_END = 2
TYPE_INSTANT = 3
TYPE_COUNTER = 4

_PROCESS_SHIFT = 60
_THREAD_SHIFT = 60
_PROCESS_BASE = 1 << _PROCESS_SHIFT
_THREAD_BASE = 2 << _THREAD_SHIFT
_COUNTER_BASE = 3 << 60


class PerfettoTrackState:
    def __init__(self) -> None:
        self._pids: set[int] = set()
        self._tids: set[tuple[int, int]] = set()
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
) -> bytes:
    result = encode_varint_field(TrackDescriptorField.UUID, uuid)
    result += encode_string_field(TrackDescriptorField.NAME, name)
    if parent_uuid is not None:
        result += encode_varint_field(TrackDescriptorField.PARENT_UUID, parent_uuid)
    if pid is not None and tid is not None:
        thread_desc = encode_varint_field(ThreadDescriptorField.PID, pid) + encode_varint_field(ThreadDescriptorField.TID, tid)
        result += encode_bytes_field(TrackDescriptorField.THREAD, thread_desc)
    if is_counter:
        result += encode_bytes_field(TrackDescriptorField.COUNTER, b"")
    return result


def build_trace_packet(
    sequence_id: int,
    timestamp: int | None = None,
    track_event: bytes | None = None,
    track_descriptor: bytes | None = None,
) -> bytes:
    result = b""
    result += encode_varint_field(TracePacketField.SEQUENCE_ID, sequence_id)
    if timestamp is not None:
        result += encode_varint_field(TracePacketField.TIMESTAMP, timestamp)
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


def _base_annotations(gen: int, iid: int) -> list[bytes]:
    return [
        _build_debug_annotation_int("generation", gen),
        _build_debug_annotation_int("iid", iid),
    ]


def convert_item_to_perfetto_packets(
    pid: int,
    item: TGCStatsInfo,
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[list[bytes], list[bytes]]:
    descriptors: list[bytes] = []
    packets: list[bytes] = []

    if not state.has_pid(pid):
        state.mark_pid(pid)
        proc_uuid = state.get_process_track_uuid(pid)
        desc = build_track_descriptor(proc_uuid, f"Process {pid}")
        descriptors.append(build_trace_packet(sequence_id, track_descriptor=desc))

    thread_uuid = state.get_thread_track_uuid(pid, item.iid)
    if not state.has_tid(pid, item.iid):
        state.mark_tid(pid, item.iid)
        proc_uuid = state.get_process_track_uuid(pid)
        desc = build_track_descriptor(
            thread_uuid,
            f"Thread {item.iid}",
            pid=pid,
            tid=item.iid,
            parent_uuid=proc_uuid,
        )
        descriptors.append(build_trace_packet(sequence_id, track_descriptor=desc))

    ts_start_ns = item.ts_start
    ts_stop_ns = item.ts_stop
    if ts_start_ns >= ts_stop_ns:
        return descriptors, packets

    ts_start_us = ts_to_us(ts_start_ns)
    ts_stop_us = ts_to_us(ts_stop_ns)

    gen = item.gen
    iid = item.iid
    base_ann = _base_annotations(gen, iid)

    pause_ann = list(base_ann)
    pause_ann.append(_build_debug_annotation_int("collections", item.collections))
    pause_ann.append(_build_debug_annotation_int("heap_size", item.heap_size))
    pause_ann.append(_build_debug_annotation_int("collected", item.collected))
    pause_ann.append(_build_debug_annotation_int("uncollectable", item.uncollectable))
    pause_ann.append(_build_debug_annotation_int("candidates", item.candidates))

    packets.append(build_trace_packet(
        sequence_id, timestamp=ts_start_us,
        track_event=_make_slice_begin(
            thread_uuid, f"GC Pause (gen={gen})", [f"gc.pause(gen={gen})"],
            pause_ann,
        ),
    ))

    if has_mark_alive(item) and item.ts_mark_alive_stop - item.ts_mark_alive_start > 0:
        ann = list(base_ann)
        ann.append(_build_debug_annotation_int("alive_size", item.alive_size))
        mark_alive_start_us = ts_to_us(item.ts_mark_alive_start)
        mark_alive_stop_us = ts_to_us(item.ts_mark_alive_stop)
        packets.append(build_trace_packet(
            sequence_id, timestamp=mark_alive_start_us,
            track_event=_make_slice_begin(
                thread_uuid, f"Mark Alive (gen={gen})", [f"gc.mark.alive(gen={gen})"],
                ann,
            ),
        ))
        packets.append(build_trace_packet(
            sequence_id, timestamp=mark_alive_stop_us,
            track_event=_make_slice_end(thread_uuid),
        ))

    if has_incremental(item) and item.ts_fill_increment_stop - item.ts_fill_increment_start > 0:
        ann = list(base_ann)
        ann.append(_build_debug_annotation_int("increment_size", item.increment_size))
        fill_inc_start_us = ts_to_us(item.ts_fill_increment_start)
        fill_inc_stop_us = ts_to_us(item.ts_fill_increment_stop)
        packets.append(build_trace_packet(
            sequence_id, timestamp=fill_inc_start_us,
            track_event=_make_slice_begin(
                thread_uuid, f"Fill increment (gen={gen})", [f"gc.increment(gen={gen})"],
                ann,
            ),
        ))
        packets.append(build_trace_packet(
            sequence_id, timestamp=fill_inc_stop_us,
            track_event=_make_slice_end(thread_uuid),
        ))

    if has_deduce_unreachable(item) and item.ts_deduce_unreachable_stop - item.ts_deduce_unreachable_start > 0:
        deduce_start_us = ts_to_us(item.ts_deduce_unreachable_start)
        deduce_stop_us = ts_to_us(item.ts_deduce_unreachable_stop)
        packets.append(build_trace_packet(
            sequence_id, timestamp=deduce_start_us,
            track_event=_make_slice_begin(
                thread_uuid, f"Deduce Unreachable (gen={gen})", [f"gc.deduce(gen={gen})"],
                list(base_ann),
            ),
        ))
        packets.append(build_trace_packet(
            sequence_id, timestamp=deduce_stop_us,
            track_event=_make_slice_end(thread_uuid),
        ))

    if has_handle_weakrefs(item) and item.ts_handle_weakref_callbacks_stop - item.ts_handle_weakref_callbacks_start > 0:
        wr_start_us = ts_to_us(item.ts_handle_weakref_callbacks_start)
        wr_stop_us = ts_to_us(item.ts_handle_weakref_callbacks_stop)
        packets.append(build_trace_packet(
            sequence_id, timestamp=wr_start_us,
            track_event=_make_slice_begin(
                thread_uuid, f"Handle Weakrefs Callbacks (gen={gen})",
                [f"gc.weakrefs(gen={gen})"],
                list(base_ann),
            ),
        ))
        packets.append(build_trace_packet(
            sequence_id, timestamp=wr_stop_us,
            track_event=_make_slice_end(thread_uuid),
        ))

    if has_finalize_garbage(item) and item.ts_finalize_garbage_stop - item.ts_handle_weakref_callbacks_stop > 0:
        ann = list(base_ann)
        ann.append(_build_debug_annotation_int("finalized_garbage_count", item.finalized_garbage_count))
        fin_start_us = ts_to_us(item.ts_handle_weakref_callbacks_stop)
        fin_stop_us = ts_to_us(item.ts_finalize_garbage_stop)
        packets.append(build_trace_packet(
            sequence_id, timestamp=fin_start_us,
            track_event=_make_slice_begin(
                thread_uuid, f"Finalize Garbage (gen={gen})", [f"gc.finalize(gen={gen})"],
                ann,
            ),
        ))
        packets.append(build_trace_packet(
            sequence_id, timestamp=fin_stop_us,
            track_event=_make_slice_end(thread_uuid),
        ))

    if has_handle_resurrected(item) and item.ts_handle_resurrected_stop - item.ts_finalize_garbage_stop > 0:
        res_start_us = ts_to_us(item.ts_finalize_garbage_stop)
        res_stop_us = ts_to_us(item.ts_handle_resurrected_stop)
        packets.append(build_trace_packet(
            sequence_id, timestamp=res_start_us,
            track_event=_make_slice_begin(
                thread_uuid, f"Handle Resurrected (gen={gen})", [f"gc.resurrect(gen={gen})"],
                list(base_ann),
            ),
        ))
        packets.append(build_trace_packet(
            sequence_id, timestamp=res_stop_us,
            track_event=_make_slice_end(thread_uuid),
        ))

    if has_clear_weakrefs(item) and item.ts_clear_weakrefs_stop - item.ts_handle_resurrected_stop > 0:
        ann = list(base_ann)
        ann.append(_build_debug_annotation_int("clear_weakrefs_count", item.clear_weakrefs_count))
        cw_start_us = ts_to_us(item.ts_handle_resurrected_stop)
        cw_stop_us = ts_to_us(item.ts_clear_weakrefs_stop)
        packets.append(build_trace_packet(
            sequence_id, timestamp=cw_start_us,
            track_event=_make_slice_begin(
                thread_uuid, f"Clear Weakrefs (gen={gen})", [f"gc.clear_weakrefs(gen={gen})"],
                ann,
            ),
        ))
        packets.append(build_trace_packet(
            sequence_id, timestamp=cw_stop_us,
            track_event=_make_slice_end(thread_uuid),
        ))

    if has_delete_garbage(item) and item.ts_delete_garbage_stop - item.ts_delete_garbage_start > 0:
        ann = list(base_ann)
        ann.append(_build_debug_annotation_int("deleted_garbage_count", item.deleted_garbage_count))
        del_start_us = ts_to_us(item.ts_delete_garbage_start)
        del_stop_us = ts_to_us(item.ts_delete_garbage_stop)
        packets.append(build_trace_packet(
            sequence_id, timestamp=del_start_us,
            track_event=_make_slice_begin(
                thread_uuid, f"Delete Garbage (gen={gen})", [f"gc.delete(gen={gen})"],
                ann,
            ),
        ))
        packets.append(build_trace_packet(
            sequence_id, timestamp=del_stop_us,
            track_event=_make_slice_end(thread_uuid),
        ))

    packets.append(build_trace_packet(
        sequence_id, timestamp=ts_stop_us,
        track_event=_make_slice_end(thread_uuid),
    ))

    counter_values: list[tuple[str, int]] = [
        ("collected", item.collected),
        ("uncollectable", item.uncollectable),
        ("candidates", item.candidates),
        ("heap_size", item.heap_size),
    ]
    if has_incremental(item) and gen < 2:
        counter_values.append(("increment_size", item.increment_size))
    if has_mark_alive(item) and gen > 0:
        counter_values.append(("alive_size", item.alive_size))
    if has_finalize_garbage(item):
        counter_values.append(("finalized_garbage_count", item.finalized_garbage_count))
    if has_delete_garbage(item):
        counter_values.append(("deleted_garbage_count", item.deleted_garbage_count))
    if has_clear_weakrefs(item):
        counter_values.append(("clear_weakrefs_count", item.clear_weakrefs_count))

    for metric, value in counter_values:
        is_new = not state.has_counter_track(pid, iid, gen, metric)
        ctr_uuid = state.get_or_create_counter_track_uuid(pid, iid, gen, metric)
        if is_new:
            desc = build_track_descriptor(
                ctr_uuid,
                f"{metric} (gen={gen})",
                parent_uuid=thread_uuid,
                is_counter=True,
            )
            descriptors.append(build_trace_packet(sequence_id, track_descriptor=desc))
        packets.append(build_trace_packet(
            sequence_id, timestamp=ts_start_us,
            track_event=_make_counter_event(ctr_uuid, value),
        ))

    return descriptors, packets


def convert_instant_to_perfetto_packet(
    pid: int,
    item: TInstantMsg,
    state: PerfettoTrackState,
    sequence_id: int,
) -> tuple[list[bytes], list[bytes]]:
    descriptors: list[bytes] = []
    packets: list[bytes] = []

    if not state.has_pid(pid):
        state.mark_pid(pid)
        proc_uuid = state.get_process_track_uuid(pid)
        desc = build_track_descriptor(proc_uuid, f"Process {pid}")
        descriptors.append(build_trace_packet(sequence_id, track_descriptor=desc))

    ts_us = ts_to_us(item.ts)
    proc_uuid = state.get_process_track_uuid(pid)
    packets.append(build_trace_packet(
        sequence_id, timestamp=ts_us,
        track_event=build_track_event(
            type=TYPE_INSTANT,
            track_uuid=proc_uuid,
            name=item.name,
        ),
    ))

    return descriptors, packets

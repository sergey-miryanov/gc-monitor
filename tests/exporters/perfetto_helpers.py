"""Shared helpers for the Perfetto wire-format tests.

Each helper here is used by more than one of the ``test_perfetto_*``
modules. Anything used by a single module stays in that module.
"""

from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import (
    TracePacket,
    TrackDescriptor,
    TrackEvent,
)

from gcmon.exporters.perfetto_format import convert_trace_events_to_perfetto
from gcmon.exporters.perfetto_process_lifetime import finalize_perfetto_packets
from gcmon.exporters.perfetto_track_state import PerfettoTrackState
from gcmon.exporters.trace_converter import convert_item_to_trace_format
from gcmon.model.data import GCStatsInfo
from gcmon.model.trace_event import ThreadTrack, TraceEvent, process_meta, thread_meta


def convert_item(
    pid: int,
    item: GCStatsInfo,
    state: PerfettoTrackState,
    sequence_id: int = 1,
) -> tuple[list[bytes], list[bytes]]:
    """Convert one ``(pid, item)`` as a single batch and finalize, so the
    returned packets include the whole ``Processes`` pair.

    Unlike ``convert_items``, this finalizes; a test that wants to see
    what convert emitted on its own wants that one instead.
    """
    gc_events = convert_item_to_trace_format(pid, item)
    meta: list[TraceEvent] = [
        process_meta(pid, f"Process {pid}"),
        thread_meta(ThreadTrack(pid, item.iid), f"Thread {item.iid}"),
    ]
    descriptors, packets = convert_trace_events_to_perfetto(
        meta + gc_events,
        state,
        sequence_id,
    )
    packets.extend(finalize_perfetto_packets(state, sequence_id))
    return descriptors, packets


def convert_items(
    items: list[tuple[int, GCStatsInfo]],
    state: PerfettoTrackState,
    sequence_id: int = 1,
) -> tuple[list[bytes], list[bytes], list[bytes]]:
    """Convert each ``(pid, item)`` as its own batch, then finalize once,
    the way ``ProtobufEventEncoder`` does across flushes.

    Returns ``(descriptors, convert_packets, closeout_packets)`` so a
    test can tell what the convert passes emitted from what the single
    closeout emitted.
    """
    descriptors: list[bytes] = []
    packets: list[bytes] = []
    for pid, item in items:
        meta: list[TraceEvent] = [
            process_meta(pid, f"Process {pid}"),
            thread_meta(ThreadTrack(pid, item.iid), f"Thread {item.iid}"),
        ]
        batch_desc, batch_packets = convert_trace_events_to_perfetto(
            meta + convert_item_to_trace_format(pid, item),
            state,
            sequence_id,
        )
        descriptors.extend(batch_desc)
        packets.extend(batch_packets)
    return descriptors, packets, finalize_perfetto_packets(state, sequence_id)


def lifetime_slices(
    packets: list[bytes],
    lifetime_uuid: int,
) -> list[tuple[int, int, str, dict[str, str | int]]]:
    """Return ``[(ts, type, name, annotations), ...]`` for the slice
    events on the ``Processes`` track, in packet order."""
    out: list[tuple[int, int, str, dict[str, str | int]]] = []
    for p in packets:
        packet = TracePacket()
        packet.ParseFromString(p)
        if not packet.HasField("track_event"):
            continue
        track_event = packet.track_event
        if track_event.track_uuid != lifetime_uuid:
            continue
        if track_event.type not in (
            TrackEvent.Type.TYPE_SLICE_BEGIN,
            TrackEvent.Type.TYPE_SLICE_END,
        ):
            continue
        annotations: dict[str, str | int] = {}
        for ann in track_event.debug_annotations:
            annotations[ann.name] = ann.string_value if ann.HasField("string_value") else ann.int_value
        out.append((packet.timestamp, track_event.type, track_event.name or "", annotations))
    return out


def parse_track_descriptor(packet_bytes: bytes) -> TrackDescriptor | None:
    """Extract the inner ``TrackDescriptor`` from a ``TracePacket``.

    Returns ``None`` if the packet is not a track-descriptor packet.
    """
    packet = TracePacket()
    packet.ParseFromString(packet_bytes)
    return packet.track_descriptor if packet.HasField("track_descriptor") else None

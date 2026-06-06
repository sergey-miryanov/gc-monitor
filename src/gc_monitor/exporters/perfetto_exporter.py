"""Perfetto binary protobuf exporter for GC monitoring data."""

import threading
from pathlib import Path
from typing import override

from ..protocol import TGCStatsInfo, TInstantMsg
from .exporter import EventsExporter
from .perfetto_format import (
    PerfettoTrackState,
    TraceField,
    convert_instant_to_perfetto_packet,
    convert_item_to_perfetto_packets,
)
from .protobuf_encoder import encode_bytes_field

TYPE_INSTANT = 3

__all__ = [
    "PerfettoExporter",
]


class PerfettoExporter(EventsExporter):
    """
    Exporter for Perfetto binary protobuf format.

    Writes TracePacket messages incrementally to a binary file.
    The output is a valid Trace protobuf message suitable for
    loading in Perfetto UI.
    """

    def __init__(
        self,
        output_path: Path,
        flush_threshold: int = 1000,
    ) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._packets: list[bytes] = []
        self._descriptors: list[bytes] = []
        self._flush_threshold = flush_threshold
        self._output_path = output_path
        self._closed = False
        self._event_count = 0
        self._track_state = PerfettoTrackState()
        self._sequence_id: int = id(self) & 0x7FFFFFFF
        self._has_written = False

    @override
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        descriptors, packets = convert_item_to_perfetto_packets(
            pid, item, self._track_state, self._sequence_id
        )
        to_flush: list[bytes] = []
        with self._lock:
            self._event_count += 1
            self._descriptors.extend(descriptors)
            self._packets.extend(packets)
            if len(self._packets) >= self._flush_threshold:
                to_flush = self._descriptors + self._packets
                self._descriptors.clear()
                self._packets.clear()
        if to_flush:
            with self._io_lock:
                self._flush(to_flush)

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        descriptors, packets = convert_instant_to_perfetto_packet(
            pid, item, self._track_state, self._sequence_id
        )
        to_flush: list[bytes] = []
        with self._lock:
            self._descriptors.extend(descriptors)
            self._packets.extend(packets)
            if len(self._packets) >= self._flush_threshold:
                to_flush = self._descriptors + self._packets
                self._descriptors.clear()
                self._packets.clear()
        if to_flush:
            with self._io_lock:
                self._flush(to_flush)

    def _flush(self, entries: list[bytes]) -> None:
        if not entries:
            return
        if not self._has_written:
            self._has_written = True
            mode = "wb"
        else:
            mode = "ab"
        with open(self._output_path, mode) as f:
            for entry in entries:
                f.write(encode_bytes_field(TraceField.PACKET, entry))
            f.flush()

    @override
    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            entries = self._descriptors + self._packets
            self._descriptors.clear()
            self._packets.clear()

        if entries:
            with self._io_lock:
                self._flush(entries)

    @override
    def get_event_count(self) -> int:
        return self._event_count

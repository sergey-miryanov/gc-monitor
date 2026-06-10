"""Perfetto binary protobuf exporter for GC monitoring data."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar, override

from ..protocol import TGCStatsInfo, TInstantMsg
from .exporter import EventsExporter
from .perfetto_format import (
    PerfettoTrackState,
    TraceField,
    convert_instant_to_perfetto_packet,
    convert_item_to_perfetto_packets,
)
from .protobuf_encoder import encode_bytes_field

logger = logging.getLogger("gcmon")

TYPE_INSTANT = 3

__all__ = [
    "PerfettoExporter",
]

TItem = TypeVar("TItem", TGCStatsInfo, TInstantMsg)
TConvert = Callable[
    [int, TItem, PerfettoTrackState, int],
    tuple[list[bytes], list[bytes]],
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
        self._track_state = PerfettoTrackState()
        self._sequence_id: int = id(self) & 0x7FFFFFFF
        self._has_written = False

    def _collect_cmdline(self, pid: int) -> list[str] | None:
        try:
            import psutil
            result = psutil.Process(pid).cmdline()
            logger.debug("Collected cmdline for PID %s: %s", pid, result)
            return result
        except Exception as exc:
            logger.warning("Could not collect cmdline for PID %s: %s", pid, exc)
            return None

    def _ensure_cmdline(self, pid: int) -> None:
        if self._track_state.get_cmdline(pid) is not None:
            return
        cmdline = self._collect_cmdline(pid)
        with self._lock:
            if self._track_state.get_cmdline(pid) is not None:
                return
            if cmdline is not None:
                self._track_state.set_cmdline(pid, cmdline)

    def _enqueue(
        self,
        pid: int,
        item: TItem,
        convert: TConvert,
    ) -> None:
        to_flush: list[bytes] = []
        with self._lock:
            descriptors, packets = convert(pid, item, self._track_state, self._sequence_id)
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
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        self._ensure_cmdline(pid)
        self._enqueue(pid, item, convert_item_to_perfetto_packets)

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        self._ensure_cmdline(pid)
        self._enqueue(pid, item, convert_instant_to_perfetto_packet)

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



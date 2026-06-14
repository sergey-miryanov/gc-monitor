"""Perfetto binary protobuf exporter for GC monitoring data."""

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import override

from ..data import ts_to_us
from ..protocol import TGCStatsInfo, TInstantMsg
from ..trace_event import TraceEvent, instant_event, process_meta, thread_meta
from .exporter import EventsExporter
from .perfetto_format import (
    PerfettoTrackState,
    TraceField,
    convert_trace_events_to_perfetto,
)
from .protobuf_encoder import encode_bytes_field
from .trace_converter import convert_item_to_trace_format

logger = logging.getLogger("gcmon")

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
        cmdline_provider: Callable[[int], list[str] | None] | None = None,
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
        self._cmdline_provider = cmdline_provider or self._default_cmdline_provider

    @staticmethod
    def _default_cmdline_provider(pid: int) -> list[str]:
        import psutil
        result = psutil.Process(pid).cmdline()
        logger.debug("Collected cmdline for PID %s: %s", pid, result)
        return result

    def _collect_cmdline(self, pid: int) -> list[str] | None:
        try:
            return self._cmdline_provider(pid)
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

    def _enqueue(self, events: list[TraceEvent]) -> None:
        to_flush: list[bytes] = []
        with self._lock:
            descriptors, packets = convert_trace_events_to_perfetto(
                events, self._track_state, self._sequence_id,
            )
            self._descriptors.extend(descriptors)
            self._packets.extend(packets)
            if len(self._packets) >= self._flush_threshold:
                to_flush = self._descriptors + self._packets
                self._descriptors.clear()
                self._packets.clear()
        if to_flush:
            with self._io_lock:
                self._flush(to_flush)

    def _build_meta(self, pid: int, iid: int | None) -> list[TraceEvent]:
        """Build ProcessMeta / ThreadMeta events that have not been seen yet."""
        meta: list[TraceEvent] = []
        if not self._track_state.has_pid(pid):
            meta.append(process_meta(pid, f"Process {pid}"))
        if iid is not None and not self._track_state.has_tid(pid, iid):
            meta.append(thread_meta(pid, iid, f"Thread {iid}"))
        return meta

    @override
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        if item.ts_start >= item.ts_stop:
            return
        self._ensure_cmdline(pid)
        meta = self._build_meta(pid, item.iid)
        events = meta + convert_item_to_trace_format(pid, item)
        self._enqueue(events)

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        self._ensure_cmdline(pid)
        meta = self._build_meta(pid, None)
        meta.append(instant_event(pid, item.name, ts_to_us(item.ts)))
        self._enqueue(meta)

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

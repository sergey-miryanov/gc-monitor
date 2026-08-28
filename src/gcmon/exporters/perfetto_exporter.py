"""Perfetto binary protobuf exporter for GC monitoring data."""

import threading
from collections.abc import Callable, Set
from pathlib import Path
from typing import override

from ..model.protocol import TGCStatsInfo, TInstantMsg, TLossMsg
from ..model.trace_event import Counter, Instant, ProcessTrack, TraceEvent
from .encoder import Codec, ProtobufEventEncoder
from .exporter import EventsExporter
from .trace_converter import convert_item_to_trace_format, convert_loss_to_trace_format

__all__ = [
    "PerfettoExporter",
]


class PerfettoExporter(EventsExporter):
    """Buffer what the monitor reports as `TraceEvent`s, and write them as
    a Perfetto trace.

    One class rather than a buffering base and a subclass on top; see
    ADR-0008.
    """

    def __init__(
        self,
        output_path: Path,
        flush_threshold: int = 1000,
        cmdline_provider: Callable[[int], list[str] | None] | None = None,
        sequence_id: int | None = None,
        codec: Codec | None = None,
    ) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._buffer: list[TraceEvent] = []
        self._flush_threshold = flush_threshold
        self._output_path = output_path
        # Held at its own type: ``record_process_liveness`` is not on the
        # ``EventEncoder`` protocol. See ADR-0011.
        self._encoder = ProtobufEventEncoder(cmdline_provider=cmdline_provider, sequence_id=sequence_id, codec=codec)
        self._closed = False
        # The previous tick's live set, to spot a pid dropping out of one.
        self._live_pids: frozenset[int] = frozenset()
        self._encoder.open(output_path)

    def _enqueue(self, events: list[TraceEvent]) -> None:
        """Buffer *events*, and hand the buffer to the encoder once it is
        full enough.

        ``_lock`` is held across the write, not dropped before it. A
        producer that took a batch out of the buffer and then queued for
        ``_io_lock`` would leave the buffer looking empty to
        ``add_process_liveness``, and the departure would reach the
        encoder ahead of events already taken to be written. The lock
        order is ``_lock`` then ``_io_lock``, and nothing takes them the
        other way round.
        """
        with self._lock:
            self._buffer.extend(events)
            if len(self._buffer) < self._flush_threshold:
                return
            to_write = self._buffer[:]
            self._buffer.clear()
            with self._io_lock:
                self._encoder.write_events(to_write)

    @override
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        self._enqueue(convert_item_to_trace_format(pid, item))

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        self._enqueue([Instant(ProcessTrack(pid), item.name, item.ts)])

    @override
    def add_rss_sample(self, pid: int, rss_bytes: int, ts_ns: int) -> None:
        self._enqueue([Counter(ProcessTrack(pid), "rss", "rss", ts_ns, rss_bytes)])

    @override
    def add_loss_event(self, pid: int, item: TLossMsg) -> None:
        self._enqueue(convert_loss_to_trace_format(pid, item))

    @override
    def add_process_liveness(self, pids: Set[int], ts_ns: int) -> None:
        """Fold one tick's liveness observations into the encoder's span
        accumulator, handing the buffer over first when the tick drops a
        pid.

        A dropped pid's span closes, so the buffer goes over ahead of the
        report rather than at the next threshold. A tick that drops
        nobody leaves the buffering alone.

        ``_io_lock`` is not optional: it guards every other touch of the
        encoder, and both a flush and ``close()`` can run on another
        thread. Without it a concurrent read-modify-write can drop a
        min/max update, and a new pid arriving mid-``close()`` can raise
        ``RuntimeError: dictionary changed size during iteration`` out of
        ``get_process_lifetimes``.
        """
        to_write: list[TraceEvent] = []
        with self._lock:
            departed = bool(self._live_pids - pids)
            self._live_pids = frozenset(pids)
            if departed:
                to_write = self._buffer[:]
                self._buffer.clear()
        with self._io_lock:
            if to_write:
                self._encoder.write_events(to_write)
            self._encoder.record_process_liveness(pids, ts_ns)

    @override
    def close(self) -> None:
        """Drain the buffer and close the encoder."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            remaining = self._buffer[:]
            self._buffer.clear()
        with self._io_lock:
            if remaining:
                self._encoder.write_events(remaining)
            self._encoder.close()

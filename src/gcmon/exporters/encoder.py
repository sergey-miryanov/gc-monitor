"""Pluggable encoder interface for trace event exporters.

An ``EventEncoder`` translates a batch of ``TraceEvent`` objects into the
bytes of a single on-disk format. A new output format arrives as a second
implementation (ADR-0008).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence, Set
from compression import zstd
from pathlib import Path
from typing import Protocol

from ..model.trace_event import ProcessMeta, TraceEvent
from .perfetto_format import (
    PerfettoTrackState,
    TraceField,
    TracePacketField,
    convert_trace_events_to_perfetto,
    finalize_perfetto_packets,
)
from .protobuf_encoder import encode_bytes_field

logger = logging.getLogger("gcmon")

_COMPRESSION_LEVEL = zstd.COMPRESSION_LEVEL_DEFAULT

__all__ = [
    "EventEncoder",
    "ProtobufEventEncoder",
    "convert_trace_events_to_perfetto",
]


class EventEncoder(Protocol):
    """Translate batches of ``TraceEvent`` into on-disk bytes."""

    def open(self, path: Path) -> None:
        """Prepare the encoder for writing to *path*."""

    def write_events(self, events: Sequence[TraceEvent]) -> None:
        """Encode and persist *events* as a single batch."""

    def close(self) -> None:
        """Finalize the output. May be a no-op for some encoders."""


class ProtobufEventEncoder:
    """Encoder for Perfetto binary protobuf format."""

    def __init__(
        self,
        cmdline_provider: Callable[[int], list[str] | None] | None = None,
        sequence_id: int | None = None,
    ) -> None:
        self._path: Path | None = None
        self._track_state = PerfettoTrackState()
        self._sequence_id: int = sequence_id if sequence_id is not None else id(self) & 0x7FFFFFFF
        self._cmdline_provider: Callable[[int], list[str] | None] = (
            cmdline_provider if cmdline_provider is not None else self._default_cmdline_provider
        )
        self._has_written: bool = False

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
        if cmdline is not None:
            self._track_state.set_cmdline(pid, cmdline)

    def open(self, path: Path) -> None:
        """Bind this encoder to *path*. One encoder writes one trace.

        The track state is per-trace -- uuid allocation, descriptor dedup,
        the ``Processes`` once-per-trace flag -- so a second trace would
        come out missing its descriptors and its whole ``Processes``
        track, with nothing raised. Construct a new encoder per file.
        """
        assert self._path is None, "one encoder writes one trace; construct a new encoder per file"
        self._path = path
        self._has_written = False

    def record_process_liveness(self, pids: Set[int], ts_ns: int) -> None:
        """Fold a whole tick's liveness observations into the
        ``Processes``-track span accumulator: *pids* are the processes
        gcmon read GC state out of at *ts_ns*.

        Kept off the ``EventEncoder`` protocol, since a liveness
        observation is neither a ``TraceEvent`` nor bytes. The caller is
        ``PerfettoExporter``, which holds a typed handle to this class;
        see ADR-0011. Writes nothing: the observations reach the file at
        ``close()``.
        """
        for pid in pids:
            self._track_state.update_process_lifetime(pid, ts_ns)

    def _write_batch(self, descriptors: Sequence[bytes], packets: Sequence[bytes]) -> None:
        """Append one batch to the trace as a single compressed packet."""
        assert self._path is not None, "open() must be called before writing"
        batch = b"".join(encode_bytes_field(TraceField.PACKET, entry) for entry in (*descriptors, *packets))
        compressed = encode_bytes_field(
            TracePacketField.ZSTD_COMPRESSED_PACKETS, zstd.compress(batch, _COMPRESSION_LEVEL)
        )
        mode = "wb" if not self._has_written else "ab"
        self._has_written = True
        with open(self._path, mode) as f:
            f.write(encode_bytes_field(TraceField.PACKET, compressed))
            f.flush()

    def write_events(self, events: Sequence[TraceEvent]) -> None:
        if not events:
            return
        assert self._path is not None, "open() must be called before write_events()"
        for event in events:
            if isinstance(event, ProcessMeta) and not self._track_state.has_pid(event.pid):
                self._ensure_cmdline(event.pid)
        descriptors, packets = convert_trace_events_to_perfetto(
            list(events),
            self._track_state,
            self._sequence_id,
        )
        if not descriptors and not packets:
            return
        self._write_batch(descriptors, packets)

    def close(self) -> None:
        """Emit the ``Processes`` track and finish the file.

        The guard is on having packets, not on having written earlier:
        liveness reaches ``_track_state`` without going through
        ``write_events``, so a run in which nothing ever collected has a
        track to emit and no bytes on disk yet. A trace with nothing at
        all still produces no file.
        """
        if self._path is None:
            return
        packets = finalize_perfetto_packets(self._track_state, self._sequence_id)
        if not packets:
            return
        self._write_batch((), packets)

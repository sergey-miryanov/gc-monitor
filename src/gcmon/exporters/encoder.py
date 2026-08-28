"""Pluggable encoder interface for trace event exporters.

An ``EventEncoder`` translates a batch of ``TraceEvent`` objects into the
bytes of a single on-disk format. A new output format arrives as a second
implementation (ADR-0008).
"""

from __future__ import annotations

import logging
import zlib
from collections.abc import Callable, Sequence, Set
from functools import partial
from pathlib import Path
from typing import NamedTuple, Protocol

from ..model.trace_event import TraceEvent
from .perfetto_format import (
    PerfettoTrackState,
    TraceField,
    TracePacketField,
    convert_trace_events_to_perfetto,
    finalize_perfetto_packets,
)
from .protobuf_encoder import encode_bytes_field

logger = logging.getLogger("gcmon")

_DEFLATE_LEVEL = 6
_ZSTD_LEVEL = 3


class Codec(NamedTuple):
    """A compressed-batch field and the compressor that fills it."""

    field: TracePacketField
    compress: Callable[[bytes], bytes]


_DEFLATE = Codec(TracePacketField.COMPRESSED_PACKETS, partial(zlib.compress, level=_DEFLATE_LEVEL))


def _resolve_codec() -> Codec:
    """The codec this interpreter can write (ADR-0022)."""
    try:
        from compression import zstd
    except ImportError:
        return _DEFLATE
    return Codec(TracePacketField.ZSTD_COMPRESSED_PACKETS, partial(zstd.compress, level=_ZSTD_LEVEL))


_CODEC = _resolve_codec()

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
        codec: Codec | None = None,
    ) -> None:
        self._path: Path | None = None
        self._track_state = PerfettoTrackState()
        self._sequence_id: int = sequence_id if sequence_id is not None else id(self) & 0x7FFFFFFF
        self._cmdline_provider: Callable[[int], list[str] | None] = (
            cmdline_provider if cmdline_provider is not None else self._default_cmdline_provider
        )
        self._has_written: bool = False
        self._codec: Codec = codec if codec is not None else _CODEC
        self._cmdline_read: set[int] = set()

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
        """Read *pid*'s command line, once per trace.

        Once, and not once per pid per batch: a pid whose command line
        cannot be read -- it has already exited, or psutil is missing --
        would otherwise cost a failed read and a warning on every flush
        for the rest of the run.
        """
        if pid in self._cmdline_read:
            return
        self._cmdline_read.add(pid)
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

        Kept off the ``EventEncoder`` protocol: a liveness observation is
        neither a ``TraceEvent`` nor bytes. See ADR-0011. Writes nothing;
        the observations reach the file at ``close()``.

        A pid missing from *pids* has its span closed, so the caller hands
        over every event it holds for that pid first.
        """
        self._track_state.observe_process_liveness(pids, ts_ns)

    def _write_batch(self, descriptors: Sequence[bytes], packets: Sequence[bytes]) -> None:
        """Append one batch to the trace as a single compressed packet."""
        assert self._path is not None, "open() must be called before writing"
        batch = b"".join(encode_bytes_field(TraceField.PACKET, entry) for entry in (*descriptors, *packets))
        compressed = encode_bytes_field(self._codec.field, self._codec.compress(batch))
        mode = "wb" if not self._has_written else "ab"
        self._has_written = True
        with open(self._path, mode) as f:
            f.write(encode_bytes_field(TraceField.PACKET, compressed))
            f.flush()

    def write_events(self, events: Sequence[TraceEvent]) -> None:
        if not events:
            return
        assert self._path is not None, "open() must be called before write_events()"
        # Ahead of the convert pass, which puts the command line on the
        # process descriptor it may be about to write.
        for event in events:
            self._ensure_cmdline(event.track.pid)
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

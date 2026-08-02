"""Pluggable encoder interface for trace event exporters.

An ``EventEncoder`` translates a batch of ``TraceEvent`` objects into the
bytes of a single on-disk format.

Two implementations are provided:

- ``JsonEventEncoder``  -- Chrome Trace Event JSON format.
- ``ProtobufEventEncoder`` -- Perfetto binary protobuf format.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence, Set
from pathlib import Path
from typing import Protocol

import msgspec

from ..data import ts_to_us
from ..trace_event import ProcessMeta, TraceEvent
from .perfetto_format import (
    PerfettoTrackState,
    TraceField,
    convert_trace_events_to_perfetto,
    finalize_perfetto_packets,
)
from .protobuf_encoder import encode_bytes_field

logger = logging.getLogger("gcmon")

__all__ = [
    "EventEncoder",
    "JsonEventEncoder",
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


class JsonEventEncoder:
    """Encoder for Chrome Trace Event JSON format."""

    def __init__(self) -> None:
        self._path: Path | None = None
        self._has_written: bool = False

    def open(self, path: Path) -> None:
        self._path = path
        self._has_written = False

    def write_events(self, events: Sequence[TraceEvent]) -> None:
        if not events:
            return
        assert self._path is not None, "open() must be called before write_events()"
        with open(self._path, "ab") as f:
            for e in events:
                d = msgspec.to_builtins(e)
                ts_ns = getattr(e, "ts", None)
                if ts_ns is not None:
                    d["ts"] = ts_to_us(ts_ns)
                if e.ph == "C" and len(e.args) == 1:
                    d["name"] = ""
                encoded = msgspec.json.encode(d)
                if not self._has_written:
                    self._has_written = True
                    f.write(b"[\n" + encoded)
                else:
                    f.write(b",\n" + encoded)
            f.flush()

    def close(self) -> None:
        assert self._path is not None, "open() must be called before close()"
        if not self._has_written:
            with open(self._path, "wb") as f:
                f.write(b"[]\n")
        else:
            with open(self._path, "ab") as f:
                f.write(b"\n]\n")


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
        """Fold a whole tick's worth of liveness observations into the
        ``Processes``-track span accumulator: *pids* are the processes
        gcmon read GC state out of at *ts_ns*.

        Not part of the ``EventEncoder`` protocol -- a liveness
        observation is neither a ``TraceEvent`` nor bytes, and the
        Chrome and JSONL encoders have nothing to do with it. The caller
        is ``PerfettoExporter``, which holds a typed handle to this
        class; see ADR-0011.

        Writes nothing. The observations reach the file at ``close()``,
        through the same ``finalize_perfetto_packets`` pass that emits
        the event-derived half of every span.
        """
        for pid in pids:
            self._track_state.update_process_lifetime(pid, ts_ns)

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
        mode = "wb" if not self._has_written else "ab"
        self._has_written = True
        with open(self._path, mode) as f:
            for entry in descriptors:
                f.write(encode_bytes_field(TraceField.PACKET, entry))
            for entry in packets:
                f.write(encode_bytes_field(TraceField.PACKET, entry))
            f.flush()

    def close(self) -> None:
        if self._path is None or not self._has_written:
            return
        packets = finalize_perfetto_packets(self._track_state, self._sequence_id)
        if packets:
            with open(self._path, "ab") as f:
                for entry in packets:
                    f.write(encode_bytes_field(TraceField.PACKET, entry))
                f.flush()

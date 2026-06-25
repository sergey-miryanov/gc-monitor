"""Pluggable encoder interface for trace event exporters.

An ``EventEncoder`` translates a batch of ``TraceEvent`` objects into the
bytes of a single on-disk format.

Two implementations are provided:

- ``JsonEventEncoder``  -- Chrome Trace Event JSON format.
- ``ProtobufEventEncoder`` -- Perfetto binary protobuf format.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

import msgspec

from ..data import ts_to_us
from ..trace_event import ProcessMeta, TraceEvent
from .perfetto_format import (
    PerfettoTrackState,
    TraceField,
    convert_trace_events_to_perfetto,
)
from .protobuf_encoder import encode_bytes_field

logger = logging.getLogger("gcmon")

__all__ = [
    "EventEncoder",
    "JsonEventEncoder",
    "ProtobufEventEncoder",
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
        self._sequence_id: int = (
            sequence_id if sequence_id is not None else id(self) & 0x7FFFFFFF
        )
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
        self._path = path
        self._has_written = False

    def write_events(self, events: Sequence[TraceEvent]) -> None:
        if not events:
            return
        assert self._path is not None, "open() must be called before write_events()"
        for event in events:
            if isinstance(event, ProcessMeta) and not self._track_state.has_pid(event.pid):
                self._ensure_cmdline(event.pid)
        descriptors, packets = convert_trace_events_to_perfetto(
            list(events), self._track_state, self._sequence_id,
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
        pass

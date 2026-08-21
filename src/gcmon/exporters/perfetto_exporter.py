"""Perfetto binary protobuf exporter for GC monitoring data."""

from collections.abc import Callable, Set
from pathlib import Path
from typing import override

from ._buffered_exporter import BufferedTraceExporter
from .encoder import ProtobufEventEncoder

__all__ = [
    "PerfettoExporter",
]


class PerfettoExporter(BufferedTraceExporter):
    """Exporter for Perfetto binary protobuf format."""

    def __init__(
        self,
        output_path: Path,
        flush_threshold: int = 1000,
        cmdline_provider: Callable[[int], list[str] | None] | None = None,
        sequence_id: int | None = None,
    ) -> None:
        encoder = ProtobufEventEncoder(cmdline_provider=cmdline_provider, sequence_id=sequence_id)
        super().__init__(
            encoder,
            output_path,
            flush_threshold,
        )
        # A second, typed handle to the same object the base holds as an
        # ``EventEncoder``. Liveness is neither a ``TraceEvent`` nor
        # bytes, so it is not on that protocol; see ADR-0011.
        self._protobuf_encoder = encoder

    @override
    def add_process_liveness(self, pids: Set[int], ts_ns: int) -> None:
        """Fold one tick's liveness observations into the encoder's span
        accumulator.

        ``_io_lock`` is not optional: it guards every other touch of the
        encoder, and both a flush and ``close()`` can run on another
        thread. Without it a concurrent read-modify-write can drop a
        min/max update, and a new pid arriving mid-``close()`` can raise
        ``RuntimeError: dictionary changed size during iteration`` out of
        ``get_process_lifetimes``.
        """
        with self._io_lock:
            self._protobuf_encoder.record_process_liveness(pids, ts_ns)

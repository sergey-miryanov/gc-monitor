"""Perfetto binary protobuf exporter for GC monitoring data."""

from collections.abc import Callable
from pathlib import Path

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
    ) -> None:
        super().__init__(
            ProtobufEventEncoder(cmdline_provider=cmdline_provider),
            output_path,
            flush_threshold,
        )

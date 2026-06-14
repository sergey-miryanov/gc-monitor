"""Chrome Trace Event format exporter for GC monitoring data."""

from pathlib import Path

from ._buffered_exporter import BufferedTraceExporter
from .encoder import JsonEventEncoder

__all__ = [
    "TraceExporter",
]


class TraceExporter(BufferedTraceExporter):
    """Exporter for Chrome Trace Event format."""

    def __init__(
        self,
        output_path: Path,
        flush_threshold: int = 1000,
    ) -> None:
        super().__init__(JsonEventEncoder(), output_path, flush_threshold)

from pathlib import Path

from .chrome_trace_exporter import TraceExporter
from .combined_exporter import CombinedTraceExporter, derive_combined_paths
from .exporter import EventsExporter
from .jsonl_exporter import JsonlExporter
from .perfetto_exporter import PerfettoExporter
from .stdout_exporter import StdoutExporter


class EventsExporterFactory:
    def __init__(self, output_format: str, output_path: Path, flush_threshold: int):
        self._output_format = output_format
        self._output_path = output_path
        self._flush_threshold = flush_threshold

    def __call__(self) -> EventsExporter:
        match self._output_format:
            case "stdout":
                return StdoutExporter(flush_threshold=self._flush_threshold)
            case "jsonl":
                return JsonlExporter(output_path=self._output_path, flush_threshold=self._flush_threshold)
            case "chrome" | "trace":
                return TraceExporter(output_path=self._output_path, flush_threshold=self._flush_threshold)
            case "perfetto":
                return PerfettoExporter(output_path=self._output_path, flush_threshold=self._flush_threshold)
            case "chrome+perfetto":
                chrome_path, perfetto_path = derive_combined_paths(self._output_path)
                chrome = TraceExporter(output_path=chrome_path, flush_threshold=self._flush_threshold)
                perfetto = PerfettoExporter(output_path=perfetto_path, flush_threshold=self._flush_threshold)
                return CombinedTraceExporter(chrome=chrome, perfetto=perfetto)
            case _:
                raise ValueError(f"Unknown output format: {self._output_format}")

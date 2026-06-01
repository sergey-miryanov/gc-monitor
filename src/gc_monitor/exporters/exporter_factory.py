from pathlib import Path

from ..lock_strategy import LockStrategy, NoLock
from .chrome_trace_exporter import TraceExporter
from .exporter import EventsExporter
from .jsonl_exporter import JsonlExporter
from .stdout_exporter import StdoutExporter


class EventsExporterFactory:
    def __init__(self, lock: type[LockStrategy], output_format: str, output_path: Path, flush_threshold: int):
        self._lock_factory = lock
        self._output_format = output_format
        self._output_path = output_path
        self._flush_threshold = flush_threshold

    def __call__(self) -> EventsExporter:
        match self._output_format:
            case "stdout":
                return StdoutExporter(NoLock, flush_threshold=self._flush_threshold)
            case "jsonl":
                return JsonlExporter(
                    self._lock_factory, output_path=self._output_path, flush_threshold=self._flush_threshold
                )
            case _:
                return TraceExporter(
                    self._lock_factory, output_path=self._output_path, flush_threshold=self._flush_threshold
                )

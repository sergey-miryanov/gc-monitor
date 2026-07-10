"""Tests for EventsExporterFactory."""

from pathlib import Path

import pytest

from gcmon.exporters.chrome_trace_exporter import TraceExporter
from gcmon.exporters.combined_exporter import CombinedTraceExporter
from gcmon.exporters.exporter_factory import EventsExporterFactory
from gcmon.exporters.jsonl_exporter import JsonlExporter
from gcmon.exporters.perfetto_exporter import PerfettoExporter
from gcmon.exporters.stdout_exporter import StdoutExporter


class TestEventsExporterFactory:
    def test_stdout_format(self, tmp_path: Path) -> None:
        factory = EventsExporterFactory("stdout", tmp_path / "out", 100)
        exporter = factory()
        assert isinstance(exporter, StdoutExporter)

    def test_jsonl_format(self, tmp_path: Path) -> None:
        factory = EventsExporterFactory("jsonl", tmp_path / "out.jsonl", 100)
        exporter = factory()
        assert isinstance(exporter, JsonlExporter)

    def test_chrome_format(self, tmp_path: Path) -> None:
        factory = EventsExporterFactory("chrome", tmp_path / "out.json", 100)
        exporter = factory()
        assert isinstance(exporter, TraceExporter)

    def test_trace_format(self, tmp_path: Path) -> None:
        factory = EventsExporterFactory("trace", tmp_path / "out.json", 100)
        exporter = factory()
        assert isinstance(exporter, TraceExporter)

    def test_perfetto_format(self, tmp_path: Path) -> None:
        factory = EventsExporterFactory("perfetto", tmp_path / "out.pb", 100)
        exporter = factory()
        assert isinstance(exporter, PerfettoExporter)

    def test_chrome_plus_perfetto_format(self, tmp_path: Path) -> None:
        factory = EventsExporterFactory("chrome+perfetto", tmp_path / "trace", 100)
        exporter = factory()
        assert isinstance(exporter, CombinedTraceExporter)
        assert exporter.chrome_path == tmp_path / "trace.json"
        assert exporter.perfetto_path == tmp_path / "trace.pftrace"

    def test_chrome_plus_perfetto_strips_extensions(self, tmp_path: Path) -> None:
        factory = EventsExporterFactory("chrome+perfetto", tmp_path / "trace.json", 100)
        exporter = factory()
        assert isinstance(exporter, CombinedTraceExporter)
        assert exporter.chrome_path == tmp_path / "trace.json"
        assert exporter.perfetto_path == tmp_path / "trace.pftrace"

    def test_unknown_format_raises_value_error(self, tmp_path: Path) -> None:
        factory = EventsExporterFactory("unknown", tmp_path / "out", 100)
        with pytest.raises(ValueError, match="Unknown output format: unknown"):
            factory()

    def test_factory_forwards_parameters(self, tmp_path: Path) -> None:
        path = tmp_path / "out.jsonl"
        factory = EventsExporterFactory("jsonl", path, 50)
        exporter = factory()
        assert isinstance(exporter, JsonlExporter)
        assert exporter._flush_threshold == 50
        assert exporter._output_path == path

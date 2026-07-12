"""Shared fixtures for exporter tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import pytest

from gcmon.exporters import JsonlExporter, PerfettoExporter, TraceExporter
from tests.helpers import JsonlRecord


class ExporterFactory(Protocol):
    def __call__(self, threshold: int = 100) -> tuple[JsonlExporter | TraceExporter | PerfettoExporter, Path]: ...


class JsonlFileReader(Protocol):
    def __call__(self, path: Path) -> list[JsonlRecord]: ...


@pytest.fixture
def jsonl_exporter(tmp_path: Path) -> ExporterFactory:
    """Factory fixture for JsonlExporter instances.

    Usage:
        exporter, path = jsonl_exporter(threshold=50)
    """

    def _make(threshold: int = 100) -> tuple[JsonlExporter, Path]:
        path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(output_path=path, flush_threshold=threshold)
        return exporter, path

    return _make


@pytest.fixture
def trace_exporter(tmp_path: Path) -> ExporterFactory:
    """Factory fixture for TraceExporter instances.

    Usage:
        exporter, path = trace_exporter(threshold=50)
    """

    def _make(threshold: int = 100) -> tuple[TraceExporter, Path]:
        path = tmp_path / "trace.json"
        exporter = TraceExporter(output_path=path, flush_threshold=threshold)
        return exporter, path

    return _make


@pytest.fixture
def perfetto_exporter(tmp_path: Path) -> ExporterFactory:
    """Factory fixture for PerfettoExporter instances.

    Usage:
        exporter, path = perfetto_exporter(threshold=50)
    """

    def _make(threshold: int = 100) -> tuple[PerfettoExporter, Path]:
        path = tmp_path / "trace.pb"
        exporter = PerfettoExporter(output_path=path, flush_threshold=threshold)
        return exporter, path

    return _make


@pytest.fixture
def read_jsonl() -> JsonlFileReader:
    """Read a JSONL file and return list of parsed events."""

    def _read(path: Path) -> list[JsonlRecord]:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    return _read

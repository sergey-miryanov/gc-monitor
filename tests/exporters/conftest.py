"""Shared fixtures for exporter tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gcmon.exporters import JsonlExporter, PerfettoExporter, TraceExporter


@pytest.fixture
def jsonl_exporter(tmp_path: Path) -> Callable[..., tuple[JsonlExporter, Path]]:
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
def trace_exporter(tmp_path: Path) -> Callable[..., tuple[TraceExporter, Path]]:
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
def perfetto_exporter(tmp_path: Path) -> Callable[..., tuple[PerfettoExporter, Path]]:
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
def read_jsonl() -> Callable[..., list[dict[str, Any]]]:
    """Read a JSONL file and return list of parsed events."""

    def _read(path: Path) -> list[dict[str, Any]]:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    return _read

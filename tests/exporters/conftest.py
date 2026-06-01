"""Shared fixtures for exporter tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gc_monitor.exporters import JsonlExporter, TraceExporter
from gc_monitor.lock_strategy import NoLock


@pytest.fixture
def jsonl_exporter(tmp_path: Path) -> Callable[..., tuple[JsonlExporter, Path]]:
    """Factory fixture for JsonlExporter instances.

    Usage:
        exporter, path = jsonl_exporter(threshold=50)
    """
    def _make(threshold: int = 100) -> tuple[JsonlExporter, Path]:
        path = tmp_path / "test.jsonl"
        exporter = JsonlExporter(NoLock, output_path=path, flush_threshold=threshold)
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
        exporter = TraceExporter(NoLock, output_path=path, flush_threshold=threshold)
        return exporter, path
    return _make


@pytest.fixture
def read_jsonl() -> Callable[..., list[dict[str, Any]]]:
    """Read a JSONL file and return list of parsed events."""
    def _read(path: Path) -> list[dict[str, Any]]:
        with open(path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    return _read

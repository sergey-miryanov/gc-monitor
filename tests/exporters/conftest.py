"""Shared fixtures and record builders for exporter tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

import pytest

from gcmon.exporters import JsonlExporter, PerfettoExporter
from gcmon.model.data import GCStatsInfo
from tests.helpers import JsonlRecord, create_jsonl_record


class ExporterFactory(Protocol):
    def __call__(self, threshold: int = 100) -> tuple[JsonlExporter | PerfettoExporter, Path]: ...


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


def make_inc_item(
    gen: int = 0,
    ts_start: int = 1000,
    ts_stop: int = 2000,
    increment_size: int = 500,
    alive_size: int = 300,
) -> GCStatsInfo:
    return GCStatsInfo(
        gen=gen,
        iid=1,
        ts_start=ts_start,
        ts_stop=ts_stop,
        collections=1,
        heap_size=100,
        collected=10,
        uncollectable=0,
        candidates=5,
        duration=1.0,
        increment_size=increment_size,
        alive_size=alive_size,
        ts_mark_alive_start=ts_start,
        ts_mark_alive_stop=ts_start + 100,
        ts_fill_increment_start=ts_start + 100,
        ts_fill_increment_stop=ts_start + 200,
        ts_deduce_unreachable_start=ts_start + 200,
        ts_deduce_unreachable_stop=ts_start + 300,
        ts_handle_weakref_callbacks_start=ts_start + 300,
        ts_handle_weakref_callbacks_stop=ts_start + 400,
        ts_finalize_garbage_stop=ts_start + 500,
        finalized_garbage_count=42,
        ts_handle_resurrected_stop=ts_start + 600,
        ts_clear_weakrefs_stop=ts_start + 700,
        clear_weakrefs_count=7,
        ts_delete_garbage_start=ts_start + 800,
        ts_delete_garbage_stop=ts_start + 900,
        deleted_garbage_count=13,
    )


def make_inc_jsonl_record(
    pid: int = 1,
    gen: int = 0,
    ts_start: int = 1000,
    ts_stop: int = 2000,
    increment_size: int = 500,
    alive_size: int = 300,
) -> dict[str, int | float]:
    record = create_jsonl_record(pid=pid, gen=gen, ts_start=ts_start, ts_stop=ts_stop)
    record.update(
        {
            "increment_size": increment_size,
            "alive_size": alive_size,
            "ts_mark_alive_start": ts_start,
            "ts_mark_alive_stop": ts_start + 100,
            "ts_fill_increment_start": ts_start + 100,
            "ts_fill_increment_stop": ts_start + 200,
            "ts_deduce_unreachable_start": ts_start + 200,
            "ts_deduce_unreachable_stop": ts_start + 300,
            "ts_handle_weakref_callbacks_start": ts_start + 300,
            "ts_handle_weakref_callbacks_stop": ts_start + 400,
            "ts_finalize_garbage_stop": ts_start + 500,
            "finalized_garbage_count": 42,
            "ts_handle_resurrected_stop": ts_start + 600,
            "ts_clear_weakrefs_stop": ts_start + 700,
            "clear_weakrefs_count": 7,
            "ts_delete_garbage_start": ts_start + 800,
            "ts_delete_garbage_stop": ts_start + 900,
            "deleted_garbage_count": 13,
        }
    )
    return record

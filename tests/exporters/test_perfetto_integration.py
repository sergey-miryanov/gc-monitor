"""Integration tests for Chrome and Perfetto trace exporters.

Drives the real ``perfetto.trace_processor`` binary against synthetic
``GCStatsInfo`` traces and asserts on the SQL tables (``slice``, ``args``,
``track``, ``counter_track``) the trace processor exposes.

Both Chrome JSON and Perfetto binary protobuf formats are exercised
identically. These tests are deselected from the default ``pytest`` run
(marker ``integration``) because the ``perfetto`` package downloads a
~100 MB binary on first use. Run with ``pytest -m integration``.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("perfetto")
from perfetto.trace_processor import TraceProcessor

from gcmon.data import GCStatsInfo
from gcmon.exporters import PerfettoExporter, TraceExporter

pytestmark = [pytest.mark.integration]

_PID: int = 12345
_PAUSE_NAME: str = "GC Pause (gen=0)"

_GEN: int = 0
_IID: int = 0
_COLLECTIONS: int = 5
_HEAP_SIZE: int = 1000
_COLLECTED: int = 10
_UNCOLLECTABLE: int = 2
_CANDIDATES: int = 3
_DURATION: float = 0.005
_TS_START: int = 1_500_000_000
_TS_STOP: int = 1_500_005_000

_EXPECTED_PAUSE_ARGS: dict[str, int] = {
    "generation": _GEN,
    "iid": _IID,
    "collections": _COLLECTIONS,
    "heap_size": _HEAP_SIZE,
    "collected": _COLLECTED,
    "uncollectable": _UNCOLLECTABLE,
    "candidates": _CANDIDATES,
}

_EXPECTED_COUNTER_NAMES: frozenset[str] = frozenset({
    "G0 collected",
    "G0 uncollectable",
    "G0 candidates",
    "G0 heap_size",
})

_ARG_PREFIX: dict[str, str] = {
    "chrome": "args",
    "perfetto": "debug",
}


def _make_item() -> GCStatsInfo:
    return GCStatsInfo(
        gen=_GEN,
        iid=_IID,
        ts_start=_TS_START,
        ts_stop=_TS_STOP,
        heap_size=_HEAP_SIZE,
        collections=_COLLECTIONS,
        collected=_COLLECTED,
        uncollectable=_UNCOLLECTABLE,
        candidates=_CANDIDATES,
        duration=_DURATION,
    )


def _write_trace(tmp: Path, fmt: str) -> Path:
    path = tmp / ("trace.json" if fmt == "chrome" else "trace.pb")
    if fmt == "chrome":
        exporter: TraceExporter | PerfettoExporter = TraceExporter(
            output_path=path, flush_threshold=1000,
        )
    else:
        exporter = PerfettoExporter(
            output_path=path,
            flush_threshold=1000,
            cmdline_provider=lambda _pid: None,
        )
    exporter.add_event(_PID, _make_item())
    exporter.close()
    return path


@pytest.fixture
def trace_processor(fmt: str) -> Iterator[TraceProcessor]:
    with tempfile.TemporaryDirectory() as td:
        path = _write_trace(Path(td), fmt)
        tp = TraceProcessor(trace=str(path))
        try:
            yield tp
        finally:
            tp.close()


class TestSliceArgs:
    """The GC Pause slice carries all pause args visible to the trace processor."""

    @pytest.mark.parametrize("fmt", ["chrome", "perfetto"])
    def test_pause_slice_exists(self, fmt: str, trace_processor: TraceProcessor) -> None:
        rows = list(trace_processor.query(
            f"SELECT name FROM slice WHERE name = '{_PAUSE_NAME}'"
        ))
        assert len(rows) == 1, f"expected exactly one '{_PAUSE_NAME}' slice, got {rows}"

    @pytest.mark.parametrize("fmt", ["chrome", "perfetto"])
    def test_pause_slice_has_all_expected_args(
        self, fmt: str, trace_processor: TraceProcessor,
    ) -> None:
        prefix = _ARG_PREFIX[fmt]
        rows = {
            r.flat_key: r.int_value
            for r in trace_processor.query(
                f"SELECT flat_key, int_value FROM args "
                f"WHERE arg_set_id = ("
                f"  SELECT arg_set_id FROM slice "
                f"  WHERE name = '{_PAUSE_NAME}' AND dur > 0"
                f")"
            )
        }
        for key, expected in _EXPECTED_PAUSE_ARGS.items():
            qualified = f"{prefix}.{key}"
            assert qualified in rows, f"missing arg {qualified}; got {sorted(rows)}"
            assert rows[qualified] == expected, (
                f"{qualified}: expected {expected}, got {rows[qualified]}"
            )


class TestCounterTracks:
    """The four counter metrics (collected/uncollectable/candidates/heap_size)
    each have a counter track with the expected name."""

    @pytest.mark.parametrize("fmt", ["chrome", "perfetto"])
    def test_all_expected_counter_tracks_present(
        self, fmt: str, trace_processor: TraceProcessor,
    ) -> None:
        rows = {r.name for r in trace_processor.query("SELECT name FROM counter_track")}
        assert _EXPECTED_COUNTER_NAMES.issubset(rows), (
            f"missing counter tracks: {_EXPECTED_COUNTER_NAMES - rows}; got {rows}"
        )

    @pytest.mark.parametrize("fmt", ["chrome", "perfetto"])
    def test_counter_track_count(self, fmt: str, trace_processor: TraceProcessor) -> None:
        rows = list(trace_processor.query("SELECT name FROM counter_track"))
        assert len(rows) == len(_EXPECTED_COUNTER_NAMES), (
            f"expected {len(_EXPECTED_COUNTER_NAMES)} counter tracks, got {len(rows)}: "
            f"{[r.name for r in rows]}"
        )


class TestTrackDescriptors:
    """The Perfetto exporter emits a process track descriptor with the
    expected ``Process <pid>`` name. (Chrome JSON does not produce a
    separate process track; the test is therefore Perfetto-only.)"""

    @pytest.mark.parametrize("fmt", ["perfetto"])
    def test_process_track_present(self, fmt: str, trace_processor: TraceProcessor) -> None:
        rows = [
            r.name for r in trace_processor.query(
                f"SELECT name FROM track WHERE name = 'Process {_PID}'"
            )
        ]
        assert rows == [f"Process {_PID}"], f"expected exactly one 'Process {_PID}' track, got {rows}"

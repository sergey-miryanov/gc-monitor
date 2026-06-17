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

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("perfetto")
from perfetto.trace_processor import TraceProcessor

from gcmon.exporters import PerfettoExporter, TraceExporter
from tests.conftest import DEFAULT_PID
from tests.data_helpers import create_instant_msg
from tests.helpers import create_mock_incremental_item, create_mock_stats_item

pytestmark = [pytest.mark.integration]

_PAUSE_NAME: str = "GC Pause (gen=0)"
_INSTANT_NAME: str = "GC monitor started"

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
    "G1 collected",
    "G1 uncollectable",
    "G1 candidates",
    "G1 heap_size",
    "G1 increment_size",
    "G1 alive_size",
    "G1 finalized_garbage_count",
    "G1 deleted_garbage_count",
    "G1 clear_weakrefs_count",
})

_ARG_PREFIX: dict[str, str] = {
    "chrome": "args",
    "perfetto": "debug",
}


def _write_trace(tmp: Path, fmt: str) -> Path:
    path = tmp / ("trace.json" if fmt == "chrome" else "trace.pb")
    exporter: TraceExporter | PerfettoExporter
    if fmt == "chrome":
        exporter = TraceExporter(
            output_path=path, flush_threshold=1000,
        )
    else:
        exporter = PerfettoExporter(
            output_path=path,
            flush_threshold=1000,
            cmdline_provider=lambda _pid: None,
        )
    exporter.add_instant_event(
        DEFAULT_PID,
        create_instant_msg(name=_INSTANT_NAME, ts=_TS_START - 1_000_000),
    )
    exporter.add_event(DEFAULT_PID, create_mock_stats_item(
        gen=_GEN, iid=_IID,
        collections=_COLLECTIONS, collected=_COLLECTED,
        uncollectable=_UNCOLLECTABLE, candidates=_CANDIDATES,
        heap_size=_HEAP_SIZE,
    ))
    exporter.add_event(DEFAULT_PID, create_mock_incremental_item(gen=1, iid=1))
    exporter.add_event(DEFAULT_PID, create_mock_stats_item(
        gen=_GEN, iid=2,
        collections=_COLLECTIONS, collected=_COLLECTED,
        uncollectable=_UNCOLLECTABLE, candidates=_CANDIDATES,
        heap_size=_HEAP_SIZE,
    ))
    exporter.close()
    return path


@pytest.fixture
def trace_processor(tmp_path: Path, fmt: str) -> Iterator[TraceProcessor]:
    path = _write_trace(tmp_path, fmt)
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
        assert len(rows) == 2, f"expected two '{_PAUSE_NAME}' slices, got {rows}"

    @pytest.mark.parametrize("fmt", ["chrome", "perfetto"])
    def test_pause_slice_has_all_expected_args(
        self, fmt: str, trace_processor: TraceProcessor,
    ) -> None:
        prefix = _ARG_PREFIX[fmt]
        rows = {
            r.flat_key: r.int_value
            for r in trace_processor.query(
                f"SELECT flat_key, int_value FROM args "
                f"WHERE arg_set_id IN ("
                f"  SELECT s.arg_set_id FROM slice s "
                f"  JOIN track t ON s.track_id = t.id "
                f"  WHERE s.name = '{_PAUSE_NAME}' AND s.dur > 0 "
                f"  AND t.name = 'Thread 0'"
                f")"
            )
        }
        for key, expected in _EXPECTED_PAUSE_ARGS.items():
            qualified = f"{prefix}.{key}"
            assert qualified in rows, f"missing arg {qualified}; got {sorted(rows)}"
            assert rows[qualified] == expected, (
                f"{qualified}: expected {expected}, got {rows[qualified]}"
            )

    @pytest.mark.parametrize("fmt", ["chrome", "perfetto"])
    def test_full_gen1_pause_slice_exists(
        self, fmt: str, trace_processor: TraceProcessor,
    ) -> None:
        rows = list(trace_processor.query(
            "SELECT name FROM slice WHERE name = 'GC Pause (gen=1)'"
        ))
        assert len(rows) == 1, f"expected exactly one 'GC Pause (gen=1)' slice, got {rows}"

    @pytest.mark.parametrize("fmt", ["chrome", "perfetto"])
    def test_full_fields_pause_encodes_all_optional_fields(
        self, fmt: str, trace_processor: TraceProcessor,
    ) -> None:
        expected_sub_slices = [
            "Mark Alive (gen=1)",
            "Fill increment (gen=1)",
            "Deduce Unreachable (gen=1)",
            "Handle Weakrefs Callbacks (gen=1)",
            "Finalize Garbage (gen=1)",
            "Handle Resurrected (gen=1)",
            "Clear Weakrefs (gen=1)",
            "Delete Garbage (gen=1)",
        ]
        slice_names = {r.name for r in trace_processor.query(
            "SELECT DISTINCT name FROM slice"
        )}
        missing = set(expected_sub_slices) - slice_names
        assert not missing, f"missing sub-slices: {missing}"

        prefix = _ARG_PREFIX[fmt]
        pause_args = {
            r.flat_key: r.int_value
            for r in trace_processor.query(
                "SELECT flat_key, int_value FROM args "
                "WHERE arg_set_id = ("
                "  SELECT arg_set_id FROM slice "
                "  WHERE name = 'GC Pause (gen=1)' AND dur > 0"
                ")"
            )
        }
        for key in (
            "increment_size", "alive_size",
            "finalized_garbage_count", "deleted_garbage_count", "clear_weakrefs_count",
        ):
            qualified = f"{prefix}.{key}"
            assert qualified in pause_args, f"missing arg {qualified}; got {sorted(pause_args)}"


class TestCounterTracks:
    """The four counter metrics (collected/uncollectable/candidates/heap_size)
    each have a counter track with the expected name, and no extra counter
    tracks are emitted."""

    @pytest.mark.parametrize("fmt", ["chrome", "perfetto"])
    def test_counter_track_names_match_expected(
        self, fmt: str, trace_processor: TraceProcessor,
    ) -> None:
        rows = {r.name for r in trace_processor.query("SELECT name FROM counter_track")}
        missing = _EXPECTED_COUNTER_NAMES - rows
        unexpected = rows - _EXPECTED_COUNTER_NAMES
        assert not missing and not unexpected, (
            f"counter track names mismatch; "
            f"missing: {missing or 'none'}; "
            f"unexpected: {unexpected or 'none'}"
        )


class TestTrackDescriptors:
    """The Perfetto exporter emits a process track descriptor with the
    expected ``Process <pid>`` name. (Chrome JSON does not produce a
    separate process track; the test is therefore Perfetto-only.)"""

    @pytest.mark.parametrize("fmt", ["perfetto"])
    def test_process_track_present(self, fmt: str, trace_processor: TraceProcessor) -> None:
        rows = [
            r.name for r in trace_processor.query(
                f"SELECT name FROM track WHERE name = 'Process {DEFAULT_PID}'"
            )
        ]
        assert rows == [f"Process {DEFAULT_PID}"], f"expected exactly one 'Process {DEFAULT_PID}' track, got {rows}"

    @pytest.mark.parametrize("fmt", ["perfetto"])
    def test_thread_tracks_present(self, fmt: str, trace_processor: TraceProcessor) -> None:
        rows = {r.name for r in trace_processor.query("SELECT name FROM track")}
        for iid in (0, 1, 2):
            assert f"Thread {iid}" in rows, f"missing 'Thread {iid}' track; got {sorted(rows)}"


class TestInstantEvents:
    """The instant event emitted at monitor start is visible to the trace
    processor as a dur=0 slice."""

    @pytest.mark.parametrize("fmt", ["chrome", "perfetto"])
    def test_instant_event_present(
        self, fmt: str, trace_processor: TraceProcessor,
    ) -> None:
        rows = list(trace_processor.query(
            f"SELECT name FROM slice "
            f"WHERE name = '{_INSTANT_NAME}' AND dur = 0"
        ))
        assert len(rows) == 1, (
            f"expected exactly one '{_INSTANT_NAME}' dur=0 slice, got {rows}"
        )

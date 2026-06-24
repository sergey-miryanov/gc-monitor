"""Tests for Chrome and Perfetto trace exporters that drive the real
``perfetto.trace_processor`` binary against synthetic ``GCStatsInfo`` traces
and assert on the SQL tables (``slice``, ``args``, ``track``,
``counter_track``) the trace processor exposes.

Both Chrome JSON and Perfetto binary protobuf formats are exercised
identically.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

from gcmon.exporters import PerfettoExporter, TraceExporter
from tests.conftest import DEFAULT_PID
from tests.data_helpers import create_instant_msg
from tests.helpers import create_mock_incremental_item, create_mock_stats_item

_PAUSE_NAME: str = "GC Pause (gen=0)"
_INSTANT_NAME: str = "GC monitor started"
_SECOND_PID: int = 67890

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

_FAKE_CMDLINE: tuple[str, ...] = ("python3", "-m", "fake_target")
_FAKE_CMDLINE_JOINED: str = " ".join(_FAKE_CMDLINE)


def _fake_cmdline_provider(pid: int) -> list[str] | None:
    """Returns a known fake cmdline for the two PIDs the trace uses and
    ``None`` for any other PID, so the encoder's ``None`` path is also
    exercised by the same callable."""
    if pid in (DEFAULT_PID, _SECOND_PID):
        return list(_FAKE_CMDLINE)
    return None


def _process_filter(pid: int) -> str:
    """SQL fragment to scope a query to a single ``pid``.

    Thread-attached slices (begin/end, counter) are joined through the
    ``thread_track``/``thread`` views. ``slice.track_id`` is the same track id
    for both ``thread_track`` and ``process_track`` views; the view that
    matches a given slice row is determined by the track's type.
    """
    return (
        f"JOIN thread_track tt ON s.track_id = tt.id "
        f"JOIN thread th ON tt.utid = th.utid "
        f"JOIN process p ON th.upid = p.upid "
        f"WHERE p.pid = {pid}"
    )


def _process_filter_instant(pid: int) -> str:
    """SQL fragment to scope an instant-event query to a single ``pid``.

    Instant events (e.g. ``GC monitor started``) are emitted on the process
    track, not on a thread track, so the join goes through ``process_track``.
    """
    return (
        f"JOIN process_track pt ON s.track_id = pt.id "
        f"JOIN process p ON pt.upid = p.upid "
        f"WHERE p.pid = {pid} AND s.dur = 0"
    )


def _write_trace(
    tmp: Path, fmt: str,
    cmdline_provider: Callable[[int], list[str] | None] = lambda _pid: None,
) -> Path:
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
            cmdline_provider=cmdline_provider,
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
    exporter.add_instant_event(
        _SECOND_PID,
        create_instant_msg(name=_INSTANT_NAME, ts=_TS_START - 2_000_000),
    )
    exporter.add_event(_SECOND_PID, create_mock_stats_item(
        gen=_GEN, iid=0,
        collections=_COLLECTIONS, collected=_COLLECTED,
        uncollectable=_UNCOLLECTABLE, candidates=_CANDIDATES,
        heap_size=_HEAP_SIZE,
    ))
    exporter.close()
    return path


@pytest.fixture
def trace_processor(tmp_path: Path, fmt: str) -> Iterator[TraceProcessor]:
    path = _write_trace(tmp_path, fmt)
    config = TraceProcessorConfig(load_timeout=300)
    tp = TraceProcessor(trace=str(path), config=config)
    try:
        yield tp
    finally:
        tp.close()


@pytest.fixture
def trace_processor_with_cmdline(
    tmp_path: Path, fmt: str,
) -> Iterator[TraceProcessor]:
    path = _write_trace(tmp_path, fmt, cmdline_provider=_fake_cmdline_provider)
    config = TraceProcessorConfig(load_timeout=300)
    tp = TraceProcessor(trace=str(path), config=config)
    try:
        yield tp
    finally:
        tp.close()


class TestSliceArgs:
    """The GC Pause slice carries all pause args visible to the trace processor."""

    @pytest.mark.parametrize("fmt", ["chrome", "perfetto"])
    def test_pause_slice_exists(self, fmt: str, trace_processor: TraceProcessor) -> None:
        rows = list(trace_processor.query(
            f"SELECT s.name FROM slice s "
            f"{_process_filter(DEFAULT_PID)} "
            f"AND s.name = '{_PAUSE_NAME}'"
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
                f"  {_process_filter(DEFAULT_PID)} "
                f"  AND s.name = '{_PAUSE_NAME}' AND s.dur > 0 "
                f"  AND th.name = 'Thread 0'"
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
            f"SELECT s.name FROM slice s "
            f"{_process_filter(DEFAULT_PID)} "
            f"AND s.name = 'GC Pause (gen=1)'"
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
            f"SELECT DISTINCT s.name FROM slice s "
            f"{_process_filter(DEFAULT_PID)}"
        )}
        missing = set(expected_sub_slices) - slice_names
        assert not missing, f"missing sub-slices: {missing}"

        prefix = _ARG_PREFIX[fmt]
        pause_args = {
            r.flat_key: r.int_value
            for r in trace_processor.query(
                "SELECT flat_key, int_value FROM args "
                "WHERE arg_set_id IN ("
                "  SELECT s.arg_set_id FROM slice s "
                f"  {_process_filter(DEFAULT_PID)} "
                "  AND s.name = 'GC Pause (gen=1)' AND s.dur > 0 "
                "  AND th.name = 'Thread 1'"
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
    tracks are emitted. The set comparison is robust to multiple processes
    emitting the same counter-track names."""

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
        rows = sorted(r.name for r in trace_processor.query(
            "SELECT name FROM track WHERE name LIKE 'Process %'"
        ))
        assert rows == sorted([f"Process {DEFAULT_PID}", f"Process {_SECOND_PID}"]), (
            f"expected process tracks for both PIDs, got {rows}"
        )

    @pytest.mark.parametrize("fmt", ["perfetto"])
    def test_thread_tracks_present(self, fmt: str, trace_processor: TraceProcessor) -> None:
        rows = {r.name for r in trace_processor.query(
            f"SELECT th.name FROM thread th "
            f"JOIN process p ON th.upid = p.upid "
            f"WHERE p.pid = {DEFAULT_PID}"
        )}
        for iid in (0, 1, 2):
            assert f"Thread {iid}" in rows, f"missing 'Thread {iid}' in DEFAULT_PID's threads; got {sorted(rows)}"


class TestDiagnosticTrackSchema:
    """Diagnostic: dump the track table to understand what columns are
    populated. Run with ``pytest -m integration -k TestDiagnosticTrackSchema -s``
    to see the output."""

    @pytest.mark.parametrize("fmt", ["perfetto"])
    def test_dump_track_schema(self, fmt: str, trace_processor: TraceProcessor) -> None:
        rows = list(trace_processor.query("PRAGMA table_info(track)"))
        for r in rows:
            print(f"COLUMN name={r.name!r} type={r.type!r} notnull={r.notnull} pk={r.pk}")

    @pytest.mark.parametrize("fmt", ["perfetto"])
    def test_dump_track_table(self, fmt: str, trace_processor: TraceProcessor) -> None:
        rows = list(trace_processor.query(
            "SELECT id, name, type, parent_id FROM track ORDER BY id"
        ))
        for r in rows:
            print(f"TRACK id={r.id} name={r.name!r} type={r.type!r} "
                  f"parent_id={r.parent_id}")

    @pytest.mark.parametrize("fmt", ["perfetto"])
    def test_dump_process_table(self, fmt: str, trace_processor: TraceProcessor) -> None:
        rows = list(trace_processor.query("SELECT * FROM process"))
        for r in rows:
            print(f"PROCESS {dict(r.__dict__)}")

    @pytest.mark.parametrize("fmt", ["perfetto"])
    def test_dump_thread_table(self, fmt: str, trace_processor: TraceProcessor) -> None:
        rows = list(trace_processor.query("SELECT * FROM thread"))
        for r in rows:
            print(f"THREAD {dict(r.__dict__)}")


class TestInstantEvents:
    """The instant event emitted at monitor start is visible to the trace
    processor as a dur=0 slice."""

    @pytest.mark.parametrize("fmt", ["chrome", "perfetto"])
    def test_instant_event_present(
        self, fmt: str, trace_processor: TraceProcessor,
    ) -> None:
        rows = list(trace_processor.query(
            f"SELECT s.name FROM slice s "
            f"{_process_filter_instant(DEFAULT_PID)} "
            f"AND s.name = '{_INSTANT_NAME}'"
        ))
        assert len(rows) == 1, (
            f"expected exactly one '{_INSTANT_NAME}' dur=0 slice for DEFAULT_PID, got {rows}"
        )


class TestCmdlineEncoding:
    """When ``cmdline_provider`` returns a non-``None`` list, the joined
    cmdline string is exposed by the trace processor as the process track's
    ``description`` arg. The Perfetto trace processor does not surface the
    per-argv ``ProcessDescriptor.CMDLINE`` repeated fields in its SQL
    tables, so the description is the only SQL-visible check."""

    def _description(self, trace_processor: TraceProcessor, pid: int) -> str | None:
        rows = list(trace_processor.query(
            f"SELECT a.string_value FROM args a "
            f"JOIN process_track pt ON a.arg_set_id = pt.source_arg_set_id "
            f"JOIN process p ON p.upid = pt.upid "
            f"WHERE p.pid = {pid} AND a.key = 'description'"
        ))
        return rows[0].string_value if rows else None

    @pytest.mark.parametrize("fmt", ["perfetto"])
    def test_cmdline_description_appears_for_known_pid(
        self, fmt: str, trace_processor_with_cmdline: TraceProcessor,
    ) -> None:
        assert (
            self._description(trace_processor_with_cmdline, DEFAULT_PID)
            == _FAKE_CMDLINE_JOINED
        )
        assert (
            self._description(trace_processor_with_cmdline, _SECOND_PID)
            == _FAKE_CMDLINE_JOINED
        )

    @pytest.mark.parametrize("fmt", ["perfetto"])
    def test_cmdline_absent_for_pid_outside_provider(
        self, fmt: str, trace_processor_with_cmdline: TraceProcessor,
    ) -> None:
        assert self._description(trace_processor_with_cmdline, 1) is None

    @pytest.mark.parametrize("fmt", ["perfetto"])
    def test_cmdline_none_for_unknown_pid(
        self, fmt: str, trace_processor: TraceProcessor,
    ) -> None:
        assert self._description(trace_processor, DEFAULT_PID) is None
        assert self._description(trace_processor, _SECOND_PID) is None

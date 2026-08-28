"""Tests for the Perfetto trace exporter that drive the real
``perfetto.trace_processor`` binary against synthetic ``GCStatsInfo`` traces
and assert on the SQL tables (``slice``, ``args``, ``track``,
``counter_track``) the trace processor exposes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from perfetto.trace_processor import TraceProcessor

from gcmon.exporters import PerfettoExporter
from tests.conftest import DEFAULT_PID
from tests.data_helpers import create_instant_msg
from tests.helpers import create_mock_incremental_item, create_mock_stats_item, open_trace_processor

_PAUSE_NAME: str = "GC Pause(0)"
_INSTANT_NAME: str = "GC monitor started"
_SECOND_PID: int = 67890
_THIRD_PID: int = 54321

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

_EXPECTED_COUNTER_NAMES: frozenset[str] = frozenset(
    {
        "G0 collected",
        "G0 uncollectable",
        "G0 candidates",
        "G0 duration",
        "G1 collected",
        "G1 uncollectable",
        "G1 candidates",
        "G1 duration",
        "Thread 0 heap_size",
        "Thread 1 heap_size",
        "Thread 2 heap_size",
    }
)

# The namespace the trace processor puts a debug annotation under.
_ARG_PREFIX: str = "debug"

_FAKE_CMDLINE: tuple[str, ...] = ("python3", "-m", "fake_target")
_FAKE_CMDLINE_JOINED: str = " ".join(_FAKE_CMDLINE)

# Name of the synthetic marker emitted on the process track so the
# cmdline description is always visible in the Perfetto UI. Must match
# ``_START_PROCESS_INSTANT_NAME`` in ``gcmon.exporters.perfetto_format``.
_START_PROCESS_MARKER_NAME: str = "Start Process"

# Name of the shared top-level Perfetto track that holds one slice per
# pid spanning the first-to-last non-meta event timestamps for that
# pid. Must match ``_PROCESS_LIFETIME_TRACK_NAME`` in
# ``gcmon.exporters.perfetto_process_lifetime``.
_PROCESS_LIFETIME_TRACK_NAME: str = "Processes"


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
    tmp: Path,
    cmdline_provider: Callable[[int], list[str] | None] = lambda _pid: None,
) -> Path:
    path = tmp / "trace.pb"
    exporter = PerfettoExporter(
        output_path=path,
        flush_threshold=1000,
        cmdline_provider=cmdline_provider,
    )
    exporter.add_instant_event(
        DEFAULT_PID,
        create_instant_msg(name=_INSTANT_NAME, ts=_TS_START - 1_000_000),
    )
    exporter.add_event(
        DEFAULT_PID,
        create_mock_stats_item(
            gen=_GEN,
            iid=_IID,
            collections=_COLLECTIONS,
            collected=_COLLECTED,
            uncollectable=_UNCOLLECTABLE,
            candidates=_CANDIDATES,
            heap_size=_HEAP_SIZE,
        ),
    )
    exporter.add_event(DEFAULT_PID, create_mock_incremental_item(gen=1, iid=1))
    exporter.add_event(
        DEFAULT_PID,
        create_mock_stats_item(
            gen=_GEN,
            iid=2,
            collections=_COLLECTIONS,
            collected=_COLLECTED,
            uncollectable=_UNCOLLECTABLE,
            candidates=_CANDIDATES,
            heap_size=_HEAP_SIZE,
        ),
    )
    exporter.add_instant_event(
        _SECOND_PID,
        create_instant_msg(name=_INSTANT_NAME, ts=_TS_START - 2_000_000),
    )
    exporter.add_event(
        _SECOND_PID,
        create_mock_stats_item(
            gen=_GEN,
            iid=0,
            collections=_COLLECTIONS,
            collected=_COLLECTED,
            uncollectable=_UNCOLLECTABLE,
            candidates=_CANDIDATES,
            heap_size=_HEAP_SIZE,
        ),
    )
    exporter.close()
    return path


def _write_trace_no_instant(
    tmp: Path,
    cmdline_provider: Callable[[int], list[str] | None] = lambda _pid: None,
) -> Path:
    path = tmp / "trace.pb"
    exporter = PerfettoExporter(
        output_path=path,
        flush_threshold=1000,
        cmdline_provider=cmdline_provider,
    )
    exporter.add_event(
        DEFAULT_PID,
        create_mock_stats_item(
            gen=_GEN,
            iid=_IID,
            collections=_COLLECTIONS,
            collected=_COLLECTED,
            uncollectable=_UNCOLLECTABLE,
            candidates=_CANDIDATES,
            heap_size=_HEAP_SIZE,
        ),
    )
    exporter.add_event(
        _SECOND_PID,
        create_mock_stats_item(
            gen=_GEN,
            iid=0,
            collections=_COLLECTIONS,
            collected=_COLLECTED,
            uncollectable=_UNCOLLECTABLE,
            candidates=_CANDIDATES,
            heap_size=_HEAP_SIZE,
        ),
    )
    exporter.close()
    return path


def _misplaced_end_events(tp: TraceProcessor) -> int:
    """Return the trace processor's ``misplaced_end_event`` counter.

    The ``stats`` table is the trace processor's own diagnostics: each
    row is a named counter the parser bumps when it hits something
    wrong. ``misplaced_end_event`` (severity ``data_loss``) counts slice
    ENDs that had nothing to close and were therefore thrown away.
    """
    rows = list(tp.query("SELECT value FROM stats WHERE name = 'misplaced_end_event'"))
    return int(rows[0].value) if rows else 0


# Timestamps for the crossing-span trace: pid A is observed first and
# dies first, but pid B starts while A is still running, so the two
# spans cross rather than nest.
_CROSS_A_START: int = 100_000_000
_CROSS_B_START: int = 200_000_000
_CROSS_A_STOP: int = 400_000_000
_CROSS_B_STOP: int = 600_000_000


def _write_crossing_trace(tmp: Path) -> Path:
    """Write a Perfetto trace whose two pids have crossing spans."""
    path = tmp / "crossing.pb"
    exporter = PerfettoExporter(output_path=path, flush_threshold=1000)
    for pid, ts in (
        (DEFAULT_PID, _CROSS_A_START),
        (_SECOND_PID, _CROSS_B_START),
        (DEFAULT_PID, _CROSS_A_STOP),
        (_SECOND_PID, _CROSS_B_STOP),
    ):
        exporter.add_instant_event(pid, create_instant_msg(name=_INSTANT_NAME, ts=ts))
    exporter.close()
    return path


@pytest.fixture
def crossing_trace_processor(tmp_path: Path) -> Iterator[TraceProcessor]:
    path = _write_crossing_trace(tmp_path)
    with open_trace_processor(path) as tp:
        yield tp


# Timestamps for the zero-duration trace. _THIRD_PID is seen at a single
# instant, so its span is zero-length as observed. DEFAULT_PID's span is
# clipped to zero by _SECOND_PID starting one nanosecond later.
_ZERO_INSTANT_TS: int = 100_000_000
_ZERO_CLIPPED_START: int = 300_000_000
_ZERO_CLIPPED_STOP: int = 800_000_000
_ZERO_CROSSER_START: int = 300_000_001
# The crosser must *outlive* the clipped span, or the two nest instead
# of crossing and nothing is clipped at all.
_ZERO_CROSSER_STOP: int = 900_000_000


def _write_zero_duration_trace(tmp: Path) -> Path:
    """Write a Perfetto trace containing both ways a ``Processes`` slice
    can end up zero-length: a pid observed at a single instant, and a pid
    clipped down to nothing by a pid starting 1ns later."""
    path = tmp / "zero.pb"
    exporter = PerfettoExporter(output_path=path, flush_threshold=1000)
    for pid, ts in (
        (_THIRD_PID, _ZERO_INSTANT_TS),
        (DEFAULT_PID, _ZERO_CLIPPED_START),
        (_SECOND_PID, _ZERO_CROSSER_START),
        (DEFAULT_PID, _ZERO_CLIPPED_STOP),
        (_SECOND_PID, _ZERO_CROSSER_STOP),
    ):
        exporter.add_instant_event(pid, create_instant_msg(name=_INSTANT_NAME, ts=ts))
    exporter.close()
    return path


@pytest.fixture
def zero_duration_trace_processor(tmp_path: Path) -> Iterator[TraceProcessor]:
    path = _write_zero_duration_trace(tmp_path)
    with open_trace_processor(path) as tp:
        yield tp


# Timestamps for the liveness trace. DEFAULT_PID collects once, early,
# and is then merely observed for the rest of the run; _SECOND_PID is
# only ever observed. Both spans start at the first observation and end
# at the last, but DEFAULT_PID's reaches back to a GC event that
# happened before gcmon ever polled it -- get_gc_stats returns
# collections that already happened.
_LIVE_TICKS: tuple[int, ...] = (300_000_000, 400_000_000, 500_000_000)
_LIVE_GC_START: int = 100_000_000
_LIVE_GC_STOP: int = 200_000_000


def _write_liveness_trace(tmp: Path) -> Path:
    """Write a Perfetto trace where one pid has both events and liveness
    and another has liveness only."""
    path = tmp / "liveness.pb"
    exporter = PerfettoExporter(output_path=path, flush_threshold=1000)
    exporter.add_event(
        DEFAULT_PID,
        create_mock_stats_item(
            gen=_GEN,
            iid=_IID,
            ts_start=_LIVE_GC_START,
            ts_stop=_LIVE_GC_STOP,
        ),
    )
    for ts in _LIVE_TICKS:
        exporter.add_process_liveness({DEFAULT_PID, _SECOND_PID}, ts)
    exporter.close()
    return path


@pytest.fixture
def liveness_trace_processor(tmp_path: Path) -> Iterator[TraceProcessor]:
    path = _write_liveness_trace(tmp_path)
    with open_trace_processor(path) as tp:
        yield tp


def _write_liveness_only_trace(tmp: Path) -> Path:
    """Write a Perfetto trace with no events whatsoever: every pid
    answered every poll and none of them ever collected."""
    path = tmp / "liveness_only.pb"
    exporter = PerfettoExporter(output_path=path, flush_threshold=1000)
    for ts in _LIVE_TICKS:
        exporter.add_process_liveness({DEFAULT_PID, _SECOND_PID}, ts)
    exporter.close()
    return path


@pytest.fixture
def liveness_only_trace_processor(tmp_path: Path) -> Iterator[TraceProcessor]:
    path = _write_liveness_only_trace(tmp_path)
    with open_trace_processor(path) as tp:
        yield tp


@pytest.fixture
def trace_processor(tmp_path: Path) -> Iterator[TraceProcessor]:
    path = _write_trace(tmp_path)
    with open_trace_processor(path) as tp:
        yield tp


@pytest.fixture
def trace_processor_with_cmdline(
    tmp_path: Path,
) -> Iterator[TraceProcessor]:
    path = _write_trace(tmp_path, cmdline_provider=_fake_cmdline_provider)
    with open_trace_processor(path) as tp:
        yield tp


@pytest.fixture
def trace_processor_no_instant(
    tmp_path: Path,
) -> Iterator[TraceProcessor]:
    path = _write_trace_no_instant(tmp_path)
    with open_trace_processor(path) as tp:
        yield tp


class TestSliceArgs:
    """The GC Pause slice carries all pause args visible to the trace processor."""

    def test_pause_slice_exists(self, trace_processor: TraceProcessor) -> None:
        rows = list(
            trace_processor.query(
                f"SELECT s.name FROM slice s {_process_filter(DEFAULT_PID)} AND s.name = '{_PAUSE_NAME}'"
            )
        )
        assert len(rows) == 2, f"expected two '{_PAUSE_NAME}' slices, got {rows}"

    def test_pause_slice_has_all_expected_args(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        prefix = _ARG_PREFIX
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
            assert rows[qualified] == expected, f"{qualified}: expected {expected}, got {rows[qualified]}"

    def test_full_gen1_pause_slice_exists(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        rows = list(
            trace_processor.query(
                f"SELECT s.name FROM slice s {_process_filter(DEFAULT_PID)} AND s.name = 'GC Pause(1)'"
            )
        )
        assert len(rows) == 1, f"expected exactly one 'GC Pause(1)' slice, got {rows}"

    def test_full_fields_pause_encodes_all_optional_fields(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        expected_sub_slices = [
            "Mark Alive(1)",
            "Fill increment(1)",
            "Deduce Unreachable(1)",
            "Handle Weakrefs Callbacks(1)",
            "Finalize Garbage(1)",
            "Handle Resurrected(1)",
            "Clear Weakrefs(1)",
            "Delete Garbage(1)",
        ]
        slice_names = {
            r.name for r in trace_processor.query(f"SELECT DISTINCT s.name FROM slice s {_process_filter(DEFAULT_PID)}")
        }
        missing = set(expected_sub_slices) - slice_names
        assert not missing, f"missing sub-slices: {missing}"

    def test_deduce_unreachable_slice_args_has_candidates(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        prefix = _ARG_PREFIX
        rows = {
            r.flat_key: r.int_value
            for r in trace_processor.query(
                "SELECT flat_key, int_value FROM args "
                "WHERE arg_set_id IN ("
                f"  SELECT s.arg_set_id FROM slice s "
                f"  {_process_filter(DEFAULT_PID)} "
                "  AND s.name = 'Deduce Unreachable(1)' AND s.dur > 0 "
                f"  AND th.name = 'Thread 1'"
                ")"
            )
        }
        assert f"{prefix}.candidates" in rows, (
            f"missing {prefix}.candidates on Deduce Unreachable(1); got {sorted(rows)}"
        )

        prefix = _ARG_PREFIX
        pause_args = {
            r.flat_key: r.int_value
            for r in trace_processor.query(
                "SELECT flat_key, int_value FROM args "
                "WHERE arg_set_id IN ("
                "  SELECT s.arg_set_id FROM slice s "
                f"  {_process_filter(DEFAULT_PID)} "
                "  AND s.name = 'GC Pause(1)' AND s.dur > 0 "
                "  AND th.name = 'Thread 1'"
                ")"
            )
        }
        for key in (
            "increment_size",
            "alive_size",
            "finalized_garbage_count",
            "deleted_garbage_count",
            "clear_weakrefs_count",
        ):
            qualified = f"{prefix}.{key}"
            assert qualified in pause_args, f"missing arg {qualified}; got {sorted(pause_args)}"


class TestCounterTracks:
    """The per-gen counter metrics (collected/uncollectable/candidates/
    duration) each have a counter track with the expected name, plus a
    shared `heap_size` track per (pid, tid). No extra counter tracks are
    emitted; in particular `increment_size` is not a counter track (it
    lives on the pause slice's args). The set comparison is robust to
    multiple processes emitting the same counter-track names."""

    def test_counter_track_names_match_expected(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        rows = {r.name for r in trace_processor.query("SELECT name FROM counter_track")}
        # One `heap_size` row per interpreter, each naming its own. The two
        # are siblings under the process track, so unqualified they would
        # read as one row drawn twice.
        normalized = {r.strip() for r in rows}
        missing = _EXPECTED_COUNTER_NAMES - normalized
        unexpected = normalized - _EXPECTED_COUNTER_NAMES
        assert not missing and not unexpected, (
            f"counter track names mismatch; missing: {missing or 'none'}; unexpected: {unexpected or 'none'}"
        )

    def test_uncollectable_counter_omitted_when_zero(
        self,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "trace.pb"
        exporter = PerfettoExporter(
            output_path=path,
            flush_threshold=1000,
        )
        exporter.add_event(
            DEFAULT_PID,
            create_mock_stats_item(
                gen=0,
                iid=0,
                uncollectable=0,
                heap_size=_HEAP_SIZE,
            ),
        )
        exporter.close()
        with open_trace_processor(path) as tp:
            names = {r.name for r in tp.query("SELECT name FROM counter_track")}
            assert "G0 uncollectable" not in {n.strip() for n in names}, (
                f"uncollectable counter should be omitted when 0; got {names}"
            )
            assert "G0 collected" in {n.strip() for n in names}
            assert "G0 candidates" in {n.strip() for n in names}

    def test_duration_counter_track_present(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        names = {
            r.name.strip()
            for r in trace_processor.query(
                "SELECT name FROM counter_track",
            )
        }
        for gen in (0, 1):
            assert f"G{gen} duration" in names, f"G{gen} duration counter should be present; got {names}"
        assert "duration" not in names, f"shared 'duration' counter should NOT be present; got {names}"

    def test_duration_counter_value_is_double(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        # The `counter` table stores both int and double values in a single
        # `value` column (DOUBLE). For the per-gen `G0 duration` track, that
        # value should equal the per-pause duration (0.005 for the default
        # fixture).
        rows = list(
            trace_processor.query(
                "SELECT id, name FROM counter_track WHERE name = 'G0 duration'",
            )
        )
        assert rows, "no G0 duration counter track found"
        for r in rows:
            values = list(
                trace_processor.query(
                    f"SELECT value FROM counter WHERE track_id = {r.id}",
                )
            )
            assert values, f"no counter values for G0 duration track {r.id}"
            assert any(abs(v.value - 0.005) < 1e-9 for v in values)

    def test_duration_counter_parented_to_gc_metrics_group(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        # Every per-gen `G{gen} duration` track should be parented to a
        # `GC Metrics` group (one per pid/iid combination).
        rows = list(
            trace_processor.query(
                "SELECT id, parent_id, name FROM track WHERE name LIKE 'G_ duration'",
            )
        )
        assert rows, "no G{gen} duration tracks found"
        for r in rows:
            assert r.parent_id, f"{r.name} track has no parent"
            parents = list(
                trace_processor.query(
                    f"SELECT name FROM track WHERE id = {r.parent_id}",
                )
            )
            assert len(parents) == 1
            assert parents[0].name == "GC Metrics"


class TestCounterYAxisShareKey:
    """SQL-level tests for the new ``y_axis_share_key`` field on
    ``CounterDescriptor``.

    The wire-level tests in ``TestCounterTrackYAxisShareKey``
    (``test_perfetto_counter_tracks.py``) are the source of truth for the
    values. This class is a forward-looking check that the values also
    survive the round-trip through the Perfetto trace processor into
    the ``counter_track`` SQL table.

    As of Perfetto 0.56.0 (pinned in ``pyproject.toml:49``), the
    ``counter_track`` SQL table does not expose ``y_axis_share_key`` as
    a column. Both tests are therefore marked ``xfail`` unconditionally
    with ``strict=False``: they will start passing automatically when
    a future Perfetto version surfaces the column, and ``strict=False``
    prevents an XPASS-and-fail flip from happening at that point.
    """

    @pytest.mark.xfail(
        reason="counter_track.y_axis_share_key not exposed in Perfetto 0.56.0",
        strict=False,
    )
    def test_y_axis_share_key_shared_across_generations(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """``G0 collected`` / ``G1 collected`` / ``G2 collected`` all
        carry the same ``y_axis_share_key`` value, and that value
        matches the metric suffix verbatim. Same for ``candidates`` and
        ``duration``. Verified via ``counter_track.y_axis_share_key``.
        """
        rows = list(
            trace_processor.query(
                "SELECT name, y_axis_share_key FROM counter_track "
                "WHERE name LIKE 'G_ %' AND name != 'heap_size' "
                "ORDER BY name",
            )
        )
        assert rows, "expected at least one G{N} <metric> track"
        by_suffix: dict[str, set[str]] = {}
        for r in rows:
            suffix = r.name.split(" ", 1)[1]
            by_suffix.setdefault(suffix, set()).add(r.y_axis_share_key)
        for suffix, keys in by_suffix.items():
            assert keys == {suffix}, (
                f"expected y_axis_share_key for metric {suffix!r} to be exactly the metric name; got {keys}"
            )

    @pytest.mark.xfail(
        reason="counter_track.y_axis_share_key not exposed in Perfetto 0.56.0",
        strict=False,
    )
    def test_heap_size_y_axis_share_key_is_null(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """The top-level ``heap_size`` track has no ``y_axis_share_key``:
        the SQL value is NULL or empty string, depending on how the
        trace processor surfaces an absent optional string field.
        """
        rows = list(
            trace_processor.query(
                "SELECT name, y_axis_share_key FROM counter_track WHERE name = 'heap_size'",
            )
        )
        assert len(rows) == 1, f"expected exactly one heap_size row, got {len(rows)}"
        r = rows[0]
        assert r.y_axis_share_key == "", f"heap_size should have no y_axis_share_key, got {r.y_axis_share_key!r}"


class TestTrackDescriptors:
    """The Perfetto exporter emits a process track descriptor with the
    expected ``Process <pid>`` name. (Chrome JSON does not produce a
    separate process track; the test is therefore Perfetto-only.)"""

    def test_process_track_present(self, trace_processor: TraceProcessor) -> None:
        rows = sorted(r.name for r in trace_processor.query("SELECT name FROM track WHERE name LIKE 'Process %'"))
        assert rows == sorted([f"Process {DEFAULT_PID}", f"Process {_SECOND_PID}"]), (
            f"expected process tracks for both PIDs, got {rows}"
        )

    def test_thread_tracks_present(self, trace_processor: TraceProcessor) -> None:
        rows = {
            r.name
            for r in trace_processor.query(
                f"SELECT th.name FROM thread th JOIN process p ON th.upid = p.upid WHERE p.pid = {DEFAULT_PID}"
            )
        }
        for iid in (0, 1, 2):
            assert f"Thread {iid}" in rows, f"missing 'Thread {iid}' in DEFAULT_PID's threads; got {sorted(rows)}"


class TestDiagnosticTrackSchema:
    """Diagnostic: dump the track table to understand what columns are
    populated. Run with ``pytest -m integration -k TestDiagnosticTrackSchema -s``
    to see the output."""

    def test_dump_track_schema(self, trace_processor: TraceProcessor) -> None:
        rows = list(trace_processor.query("PRAGMA table_info(track)"))
        for r in rows:
            print(f"COLUMN name={r.name!r} type={r.type!r} notnull={r.notnull} pk={r.pk}")

    def test_dump_track_table(self, trace_processor: TraceProcessor) -> None:
        rows = list(trace_processor.query("SELECT id, name, type, parent_id FROM track ORDER BY id"))
        for r in rows:
            print(f"TRACK id={r.id} name={r.name!r} type={r.type!r} parent_id={r.parent_id}")

    def test_dump_process_table(self, trace_processor: TraceProcessor) -> None:
        rows = list(trace_processor.query("SELECT * FROM process"))
        for r in rows:
            print(f"PROCESS {dict(r.__dict__)}")

    def test_dump_thread_table(self, trace_processor: TraceProcessor) -> None:
        rows = list(trace_processor.query("SELECT * FROM thread"))
        for r in rows:
            print(f"THREAD {dict(r.__dict__)}")


class TestInstantEvents:
    """The instant event emitted at monitor start is visible to the trace
    processor as a dur=0 slice."""

    def test_instant_event_present(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        rows = list(
            trace_processor.query(
                f"SELECT s.name FROM slice s {_process_filter_instant(DEFAULT_PID)} AND s.name = '{_INSTANT_NAME}'"
            )
        )
        assert len(rows) == 1, f"expected exactly one '{_INSTANT_NAME}' dur=0 slice for DEFAULT_PID, got {rows}"


class TestCmdlineEncoding:
    """When ``cmdline_provider`` returns a non-``None`` list, the joined
    cmdline string is exposed by the trace processor as the process track's
    ``description`` arg. The Perfetto trace processor does not surface the
    per-argv ``ProcessDescriptor.CMDLINE`` repeated fields in its SQL
    tables, so the description is the only SQL-visible check."""

    def _description(self, trace_processor: TraceProcessor, pid: int) -> str | None:
        rows = list(
            trace_processor.query(
                f"SELECT a.string_value FROM args a "
                f"JOIN process_track pt ON a.arg_set_id = pt.source_arg_set_id "
                f"JOIN process p ON p.upid = pt.upid "
                f"WHERE p.pid = {pid} AND a.key = 'description'"
            )
        )
        return rows[0].string_value if rows else None

    def test_cmdline_description_appears_for_known_pid(
        self,
        trace_processor_with_cmdline: TraceProcessor,
    ) -> None:
        assert self._description(trace_processor_with_cmdline, DEFAULT_PID) == _FAKE_CMDLINE_JOINED
        assert self._description(trace_processor_with_cmdline, _SECOND_PID) == _FAKE_CMDLINE_JOINED

    def test_cmdline_absent_for_pid_outside_provider(
        self,
        trace_processor_with_cmdline: TraceProcessor,
    ) -> None:
        assert self._description(trace_processor_with_cmdline, 1) is None

    def test_cmdline_none_for_unknown_pid(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        assert self._description(trace_processor, DEFAULT_PID) is None
        assert self._description(trace_processor, _SECOND_PID) is None


class TestStartProcessMarker:
    """The Perfetto encoder emits a single synthetic dur-0 ``Start Process``
    instant event on the process track itself, lazily on the first
    non-meta event for the pid. This guarantees the process track has at
    least one event so its ``description`` (the joined cmdline) is
    always visible in the Perfetto UI, independent of whether the caller
    emitted any ``Instant`` for the pid.
    """

    def test_marker_emitted_with_user_instant(
        self,
        trace_processor_with_cmdline: TraceProcessor,
    ) -> None:
        """The user-provided instant events (``GC monitor started``) and
        the synthetic marker (``Start Process``) both land on the
        process track. Verify one marker per pid."""
        markers = list(
            trace_processor_with_cmdline.query(
                f"SELECT p.pid FROM slice s "
                f"JOIN process_track pt ON s.track_id = pt.id "
                f"JOIN process p ON pt.upid = p.upid "
                f"WHERE s.name = '{_START_PROCESS_MARKER_NAME}' AND s.dur = 0 "
                f"ORDER BY p.pid"
            )
        )
        assert [r.pid for r in markers] == [DEFAULT_PID, _SECOND_PID], (
            f"expected one {_START_PROCESS_MARKER_NAME!r} marker per pid, got {markers}"
        )

    def test_marker_emitted_without_user_instant(
        self,
        trace_processor_no_instant: TraceProcessor,
    ) -> None:
        """This is the regression case: the caller never calls
        ``add_instant_event``, so the process track would otherwise be
        empty and the UI would hide the description. The marker keeps
        the process track rendered."""
        markers = list(
            trace_processor_no_instant.query(
                f"SELECT p.pid FROM slice s "
                f"JOIN process_track pt ON s.track_id = pt.id "
                f"JOIN process p ON pt.upid = p.upid "
                f"WHERE s.name = '{_START_PROCESS_MARKER_NAME}' AND s.dur = 0 "
                f"ORDER BY p.pid"
            )
        )
        assert [r.pid for r in markers] == [DEFAULT_PID, _SECOND_PID], (
            f"expected one {_START_PROCESS_MARKER_NAME!r} marker per pid "
            f"even without user instant events, got {markers}"
        )

    def test_marker_at_first_event_timestamp(
        self,
        trace_processor_with_cmdline: TraceProcessor,
    ) -> None:
        """The marker is placed at the timestamp of the first non-meta
        event for the pid, not at 0."""
        rows = list(
            trace_processor_with_cmdline.query(
                f"SELECT p.pid, s.ts FROM slice s "
                f"JOIN process_track pt ON s.track_id = pt.id "
                f"JOIN process p ON pt.upid = p.upid "
                f"WHERE s.name = '{_START_PROCESS_MARKER_NAME}' AND s.dur = 0 "
                f"ORDER BY p.pid"
            )
        )
        assert len(rows) == 2
        for r in rows:
            assert r.ts > 0, f"expected marker ts > 0 for pid {r.pid}, got {r.ts}"


class TestProcessesTrack:
    """The Perfetto encoder emits a single shared top-level track named
    ``Processes`` that holds one ``TYPE_SLICE_BEGIN`` /
    ``TYPE_SLICE_END`` pair per pid, spanning the first-to-last
    non-counter non-meta event timestamps for that pid.
    """

    def test_track_present(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """The ``Processes`` track is present exactly once."""
        rows = list(trace_processor.query(f"SELECT name FROM track WHERE name = '{_PROCESS_LIFETIME_TRACK_NAME}'"))
        assert len(rows) == 1, (
            f"expected exactly one {_PROCESS_LIFETIME_TRACK_NAME!r} track, got {[r.name for r in rows]}"
        )

    def test_slice_per_pid(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """There is exactly one BEGIN+END pair per pid on the
        ``Processes`` track, at the right timestamps.

        Asserting the timestamps and not just the row count matters:
        a crossing pair leaves the row count intact while silently
        handing one pid a duration that is not its own.

        This fixture's two spans cross. ``_SECOND_PID`` is observed from
        ``_TS_START - 2ms`` to ``_TS_START + 5ms``; ``DEFAULT_PID`` starts
        1ms later and runs 4ms longer. So ``_SECOND_PID``'s end is
        clipped back to just before ``DEFAULT_PID`` begins, collapsing a
        7ms span to 1ms, and ``DEFAULT_PID`` keeps its full 10ms. Before
        the clip, the trace processor reported ``DEFAULT_PID`` as
        6_000_000ns long against a real span of 10_000_000ns.
        """
        rows = list(
            trace_processor.query(
                f"SELECT s.name, s.ts, s.dur FROM slice s "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' "
                f"ORDER BY s.name"
            )
        )
        assert [r.name for r in rows] == [
            f"Process {DEFAULT_PID}",
            f"Process {_SECOND_PID}",
        ], f"expected exactly one dur-bearing Process <pid> slice per pid, got {[(r.name, r.dur) for r in rows]}"
        for r in rows:
            assert r.dur > 0, f"slice {r.name!r} has dur={r.dur}, expected > 0"
        spans = {r.name: (r.ts, r.ts + r.dur) for r in rows}
        default_start = _TS_START - 1_000_000
        assert spans == {
            f"Process {DEFAULT_PID}": (default_start, _TS_START + 9_000_000),
            f"Process {_SECOND_PID}": (_TS_START - 2_000_000, default_start - 1),
        }

    def test_every_slice_records_its_real_span(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """Both slices carry the span gcmon observed, whether or not the
        drawing survived it. ``_SECOND_PID`` is the one clipped in this
        fixture: its slice draws to ``default_start - 1`` but records the
        real end 5ms later. ``DEFAULT_PID`` is untouched and records the
        same span it draws -- read the same way, no branch needed."""
        rows = list(
            trace_processor.query(
                f"SELECT s.name AS name, a.flat_key AS flat_key, a.int_value AS int_value "
                f"FROM args a "
                f"JOIN slice s ON s.arg_set_id = a.arg_set_id "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' "
                f"AND a.flat_key IN ('debug.real_start_ts', 'debug.real_end_ts') "
                f"ORDER BY s.name, a.flat_key"
            )
        )
        assert {(r.name, r.flat_key): r.int_value for r in rows} == {
            (f"Process {DEFAULT_PID}", "debug.real_start_ts"): _TS_START - 1_000_000,
            (f"Process {DEFAULT_PID}", "debug.real_end_ts"): _TS_START + 9_000_000,
            (f"Process {_SECOND_PID}", "debug.real_start_ts"): _TS_START - 2_000_000,
            (f"Process {_SECOND_PID}", "debug.real_end_ts"): _TS_START + 5_000_000,
        }

    def test_no_misplaced_end_events(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """The trace processor discards nothing.

        ``misplaced_end_event`` counts every ``TYPE_SLICE_END`` that had
        no slice to close. It is the trace processor reporting data loss
        directly, rather than an inference from the slice table.
        """
        assert _misplaced_end_events(trace_processor) == 0

    def test_slice_name_format(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """Every slice name on the ``Processes`` track matches the
        ``Process <pid>`` pattern."""
        import re

        rows = list(
            trace_processor.query(
                f"SELECT s.name FROM slice s "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}'"
            )
        )
        pat = re.compile(r"^Process \d+$")
        for r in rows:
            assert pat.match(r.name), (
                f"slice name {r.name!r} on the {_PROCESS_LIFETIME_TRACK_NAME!r} track must match 'Process <pid>'"
            )

    def test_begin_end_match_first_last_event(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """For each pid, the slice BEGIN is at the first non-meta event
        ts, and the slice END (BEGIN + dur) is at the last Begin/End/
        Instant event ts (counter events excluded)."""
        # The fixture adds an instant event at _TS_START - 1_000_000,
        # then a GC item at _TS_START / _TS_START + dur, then a second
        # item etc. The first non-meta event for each pid is the
        # instant event. The last non-counter non-meta event for each
        # pid is the end of the last GC item's pause.
        #
        # We compare against SQL: take the min(ts) and max(ts) of all
        # Begin/End/Instant events for the pid (joined through
        # thread_track for EndEvents and through process_track for
        # Instants), then verify the slice matches.
        for pid in (DEFAULT_PID, _SECOND_PID):
            # First non-meta ts: min over all slices on both
            # process_track (instant events) and thread_track (Begin/
            # End events) for this pid.
            candidates: list[int] = []
            for join_clause in (
                f"JOIN process_track pt ON s.track_id = pt.id JOIN process p ON pt.upid = p.upid WHERE p.pid = {pid}",
                f"JOIN thread_track tt ON s.track_id = tt.id "
                f"JOIN thread th ON tt.utid = th.utid "
                f"JOIN process p ON th.upid = p.upid "
                f"WHERE p.pid = {pid}",
            ):
                rows = trace_processor.query(f"SELECT MIN(s.ts) AS ts FROM slice s {join_clause}")
                for r in rows:
                    candidates.append(r.ts)
            assert candidates, f"no first event found for pid {pid}"
            expected_first = min(candidates)

            slice_rows = list(
                trace_processor.query(
                    f"SELECT s.ts, s.dur FROM slice s "
                    f"JOIN track t ON s.track_id = t.id "
                    f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' "
                    f"AND s.name = 'Process {pid}' "
                    f"AND s.dur > 0"
                )
            )
            assert len(slice_rows) == 1, f"expected exactly one duration-bearing Process {pid} slice, got {slice_rows}"
            assert slice_rows[0].ts == expected_first, (
                f"slice begin ts mismatch for pid {pid}: got {slice_rows[0].ts}, expected {expected_first}"
            )

    def test_cmdline_arg_present(
        self,
        trace_processor_with_cmdline: TraceProcessor,
    ) -> None:
        """Each ``Process <pid>`` slice on the ``Processes`` track
        carries a ``cmdline`` debug annotation whose value is the
        argv joined with single spaces."""
        for pid in (DEFAULT_PID, _SECOND_PID):
            rows = list(
                trace_processor_with_cmdline.query(
                    f"SELECT a.string_value AS string_value "
                    f"FROM args a "
                    f"WHERE a.flat_key = 'debug.cmdline' "
                    f"AND a.arg_set_id IN ("
                    f"  SELECT s.arg_set_id FROM slice s "
                    f"  JOIN track t ON s.track_id = t.id "
                    f"  WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' "
                    f"  AND s.name = 'Process {pid}'"
                    f")"
                )
            )
            assert len(rows) == 1, f"expected exactly one debug.cmdline arg for pid {pid}, got {rows}"
            assert rows[0].string_value == _FAKE_CMDLINE_JOINED, (
                f"debug.cmdline for pid {pid}: expected {_FAKE_CMDLINE_JOINED!r}, got {rows[0].string_value!r}"
            )


class TestCrossingProcessSpans:
    """Two pids whose observed spans cross rather than nest.

    Slices on one Perfetto track are a stack, so a crossing pair cannot
    be expressed: the trace processor closes both slices at the earlier
    END and discards the later one. Before the encoder clipped these
    spans, this trace produced ``misplaced_end_event: 1`` and handed
    ``_SECOND_PID`` a duration ending at ``DEFAULT_PID``'s last event.
    """

    def test_no_misplaced_end_events(self, crossing_trace_processor: TraceProcessor) -> None:
        assert _misplaced_end_events(crossing_trace_processor) == 0

    def test_earlier_span_is_clipped_and_later_span_is_intact(
        self,
        crossing_trace_processor: TraceProcessor,
    ) -> None:
        rows = list(
            crossing_trace_processor.query(
                f"SELECT s.name, s.ts, s.dur FROM slice s "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' "
                f"ORDER BY s.ts"
            )
        )
        spans = {r.name: (r.ts, r.ts + r.dur) for r in rows}
        assert spans == {
            # Clipped to one nanosecond before the later pid begins.
            f"Process {DEFAULT_PID}": (_CROSS_A_START, _CROSS_B_START - 1),
            # Untouched: this is the span that used to be truncated.
            f"Process {_SECOND_PID}": (_CROSS_B_START, _CROSS_B_STOP),
        }

    def test_every_slice_records_its_real_span(
        self,
        crossing_trace_processor: TraceProcessor,
    ) -> None:
        """Both slices carry ``real_start_ts`` / ``real_end_ts``, so the
        drawn duration can always be told apart from the observed one --
        including for the clipped slice, whose drawn end is 200ms short
        of the truth."""
        rows = list(
            crossing_trace_processor.query(
                f"SELECT s.name AS name, a.flat_key AS flat_key, a.int_value AS int_value "
                f"FROM args a "
                f"JOIN slice s ON s.arg_set_id = a.arg_set_id "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' "
                f"AND a.flat_key IN ('debug.real_start_ts', 'debug.real_end_ts') "
                f"ORDER BY s.name, a.flat_key"
            )
        )
        assert {(r.name, r.flat_key): r.int_value for r in rows} == {
            (f"Process {DEFAULT_PID}", "debug.real_start_ts"): _CROSS_A_START,
            (f"Process {DEFAULT_PID}", "debug.real_end_ts"): _CROSS_A_STOP,
            (f"Process {_SECOND_PID}", "debug.real_start_ts"): _CROSS_B_START,
            (f"Process {_SECOND_PID}", "debug.real_end_ts"): _CROSS_B_STOP,
        }


class TestZeroDurationProcessSpans:
    """A ``Processes`` slice that ends up zero-length is still drawn.

    Two ways to get one: a pid observed at a single instant, and a pid
    clipped down to nothing by a pid starting one nanosecond later. Both
    are in this fixture. Dropping such a slice would leave the pid off
    the track with nothing to indicate it was ever monitored, and a
    reader has no way to notice an absence.
    """

    def test_no_misplaced_end_events(self, zero_duration_trace_processor: TraceProcessor) -> None:
        """A zero-duration slice is a BEGIN and an END at the same ts.
        The trace processor must pair them, not orphan the END."""
        assert _misplaced_end_events(zero_duration_trace_processor) == 0

    def test_every_pid_keeps_a_slice(
        self,
        zero_duration_trace_processor: TraceProcessor,
    ) -> None:
        """All three pids appear, two of them with ``dur = 0``."""
        rows = list(
            zero_duration_trace_processor.query(
                f"SELECT s.name AS name, s.ts AS ts, s.dur AS dur FROM slice s "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' "
                f"ORDER BY s.ts"
            )
        )
        assert {r.name: (r.ts, r.dur) for r in rows} == {
            f"Process {_THIRD_PID}": (_ZERO_INSTANT_TS, 0),
            f"Process {DEFAULT_PID}": (_ZERO_CLIPPED_START, 0),
            f"Process {_SECOND_PID}": (_ZERO_CROSSER_START, _ZERO_CROSSER_STOP - _ZERO_CROSSER_START),
        }

    def test_zero_duration_slices_still_record_their_real_span(
        self,
        zero_duration_trace_processor: TraceProcessor,
    ) -> None:
        """This is the whole point of drawing them: ``DEFAULT_PID`` draws
        as ``dur = 0`` but was observed for 500ms, and that is readable
        from the trace."""
        rows = list(
            zero_duration_trace_processor.query(
                f"SELECT s.name AS name, a.flat_key AS flat_key, a.int_value AS int_value "
                f"FROM args a "
                f"JOIN slice s ON s.arg_set_id = a.arg_set_id "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' "
                f"AND a.flat_key IN ('debug.real_start_ts', 'debug.real_end_ts') "
                f"ORDER BY s.name, a.flat_key"
            )
        )
        assert {(r.name, r.flat_key): r.int_value for r in rows} == {
            (f"Process {DEFAULT_PID}", "debug.real_start_ts"): _ZERO_CLIPPED_START,
            (f"Process {DEFAULT_PID}", "debug.real_end_ts"): _ZERO_CLIPPED_STOP,
            (f"Process {_SECOND_PID}", "debug.real_start_ts"): _ZERO_CROSSER_START,
            (f"Process {_SECOND_PID}", "debug.real_end_ts"): _ZERO_CROSSER_STOP,
            (f"Process {_THIRD_PID}", "debug.real_start_ts"): _ZERO_INSTANT_TS,
            (f"Process {_THIRD_PID}", "debug.real_end_ts"): _ZERO_INSTANT_TS,
        }


class TestMonitorReportedLiveness:
    """``Processes`` slices span what gcmon *observed*, not what it saw
    collect, so the monitor loop's per-tick liveness reports reach the
    track alongside the events. See ADR-0011.
    """

    def test_no_misplaced_end_events(self, liveness_trace_processor: TraceProcessor) -> None:
        """The two spans co-terminate on the last tick, so the later one
        nests inside the earlier and both ENDs land on one timestamp.
        The trace processor must still pair them."""
        assert _misplaced_end_events(liveness_trace_processor) == 0

    def test_liveness_only_pid_gets_exactly_one_slice(
        self,
        liveness_trace_processor: TraceProcessor,
    ) -> None:
        """``_SECOND_PID`` produced no events at all: no process
        descriptor, no process track, nothing but three liveness
        observations."""
        rows = list(
            liveness_trace_processor.query(
                f"SELECT s.ts AS ts, s.dur AS dur FROM slice s "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' AND s.name = 'Process {_SECOND_PID}'"
            )
        )
        assert len(rows) == 1, f"expected exactly one slice for the liveness-only pid, got {len(rows)}"
        assert (rows[0].ts, rows[0].ts + rows[0].dur) == (_LIVE_TICKS[0], _LIVE_TICKS[-1])

    def test_a_pid_with_both_spans_their_union(
        self,
        liveness_trace_processor: TraceProcessor,
    ) -> None:
        """Liveness folds in alongside events rather than replacing them.
        ``DEFAULT_PID``'s GC event predates every observation -- a poll
        returns collections that already happened -- so the start is the
        event's and the end is the last tick's."""
        rows = list(
            liveness_trace_processor.query(
                f"SELECT a.flat_key AS flat_key, a.int_value AS int_value FROM args a "
                f"JOIN slice s ON s.arg_set_id = a.arg_set_id "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' AND s.name = 'Process {DEFAULT_PID}' "
                f"AND a.flat_key IN ('debug.real_start_ts', 'debug.real_end_ts')"
            )
        )
        assert {r.flat_key: r.int_value for r in rows} == {
            "debug.real_start_ts": _LIVE_GC_START,
            "debug.real_end_ts": _LIVE_TICKS[-1],
        }

    def test_every_polled_pid_appears_exactly_once(
        self,
        liveness_trace_processor: TraceProcessor,
    ) -> None:
        rows = list(
            liveness_trace_processor.query(
                f"SELECT s.name AS name, COUNT(*) AS n FROM slice s "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' GROUP BY s.name"
            )
        )
        assert {r.name: r.n for r in rows} == {
            f"Process {DEFAULT_PID}": 1,
            f"Process {_SECOND_PID}": 1,
        }


class TestLivenessOnlyTrace:
    """A whole run in which nothing ever collected: no events, so no
    process descriptors, no thread tracks, no root descriptor, only the
    ``Processes`` track. The trace processor still has to accept it,
    since an idle or short-lived target is an ordinary thing to
    monitor."""

    def test_no_misplaced_end_events(self, liveness_only_trace_processor: TraceProcessor) -> None:
        assert _misplaced_end_events(liveness_only_trace_processor) == 0

    def test_both_pids_span_the_observed_range(
        self,
        liveness_only_trace_processor: TraceProcessor,
    ) -> None:
        rows = list(
            liveness_only_trace_processor.query(
                f"SELECT s.name AS name, s.ts AS ts, s.dur AS dur FROM slice s "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' ORDER BY s.name"
            )
        )
        span = (_LIVE_TICKS[0], _LIVE_TICKS[-1] - _LIVE_TICKS[0])
        assert {r.name: (r.ts, r.dur) for r in rows} == {
            f"Process {DEFAULT_PID}": span,
            f"Process {_SECOND_PID}": span,
        }


@pytest.mark.stress
class TestMultiFlushProcessesTrack:
    """Multi-flush stress test for the ``Processes`` track slice END.

    When the buffered exporter's ``flush_threshold`` is small enough to
    force many flushes for a single pid, the ``Processes``-track slice
    for that pid must end at the very last non-counter non-meta event
    ts across all flushes, not the first batch's last event. (Without
    the fix, the closeout emitted a slice END at the end of every
    convert call, so the trace processor paired the BEGIN with the
    first END and dropped the rest as orphan ENDs.)
    """

    def test_slice_end_is_last_event_ts(self, tmp_path: Path) -> None:
        pid = DEFAULT_PID
        n_items = 30
        # flush_threshold=5 forces ~6+ flushes for the n_items=30 GC
        # items plus the leading instant event.
        path = tmp_path / "trace.pftrace"
        exporter = PerfettoExporter(output_path=path, flush_threshold=5)
        try:
            exporter.add_instant_event(
                pid,
                create_instant_msg(name=_INSTANT_NAME, ts=0),
            )
            for i in range(n_items):
                ts_start = 1_000_000 * (i + 1)
                ts_stop = ts_start + 50_000
                exporter.add_event(
                    pid,
                    create_mock_stats_item(
                        gen=0,
                        iid=i,
                        collections=1,
                        collected=10,
                        uncollectable=0,
                        candidates=5,
                        heap_size=1000,
                        ts_start=ts_start,
                        ts_stop=ts_stop,
                    ),
                )
        finally:
            exporter.close()

        with open_trace_processor(path) as tp:
            rows = list(
                tp.query(
                    f"SELECT s.ts, s.dur FROM slice s "
                    f"JOIN track t ON s.track_id = t.id "
                    f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' "
                    f"AND s.name = 'Process {pid}'"
                )
            )
            assert len(rows) == 1, f"expected exactly one Processes-track slice for pid {pid}, got {rows}"
            slice_ts = rows[0].ts
            slice_dur = rows[0].dur
            slice_end = slice_ts + slice_dur
            # Expected end: the end of the last GC item's pause.
            expected_end = 1_000_000 * n_items + 50_000
            assert slice_end == expected_end, (
                f"slice end mismatch: got {slice_end}, expected "
                f"{expected_end} (last non-counter non-meta event ts); "
                f"dur={slice_dur}, ts={slice_ts}"
            )
            # Also assert BEGIN is at the first non-meta event ts
            # (the instant event at ts=0).
            assert slice_ts == 0, f"slice begin ts mismatch: got {slice_ts}, expected 0"


class TestProcessOrderingIntegration:
    """Schema-validity guard for the new root track descriptor and the
    per-process ``sibling_order_rank`` field.

    The wire-level tests in ``TestProcessOrderingByFirstTs`` (test_perfetto_ordering.py)
    are the source of truth for the rank values; this class verifies that the
    Perfetto trace processor accepts the new protobuf layout (root descriptor
    with ``process_ordering`` / ``thread_ordering`` and process descriptors
    with ``sibling_order_rank``) and that the existing ``process`` / ``track``
    SQL tables are not regressed by the new fields.

    The trace processor does not expose ``sibling_order_rank`` as a SQL
    column - it is a UI rendering hint consumed by the Perfetto UI at
    render time. Therefore these tests can verify the trace is *valid*
    and that the process tracks are still recognized, but not the
    actual UI display order. UI ordering is verifiable only in the
    Perfetto UI itself.
    """

    def test_root_descriptor_does_not_appear_as_a_track_row(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """The root descriptor (``uuid=0``) carries no ``name`` and no
        ``process``/``thread``/``counter`` sub-message, so it must NOT
        produce a row in the ``track`` SQL table. We check for this by
        asserting that no track has a NULL ``type`` column; every track
        in the table should be a recognized kind (process_track_event,
        thread_execution, counter, etc.). The NULL-name rows in the
        table correspond to ``thread_execution`` tracks and are
        therefore not related to the root descriptor.
        """
        rows = list(
            trace_processor.query(
                "SELECT id FROM track WHERE type IS NULL",
            )
        )
        assert rows == [], (
            f"root track descriptor should not create a track row with unknown type; got ids {[r.id for r in rows]}"
        )

    def test_process_table_unchanged_after_ranking(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """The ``process`` SQL table must still contain one row per pid
        that emitted events, and no more. Adding ``sibling_order_rank``
        to the process track descriptor must not change the cardinality
        or pid column.
        """
        rows = list(
            trace_processor.query(
                f"SELECT pid FROM process WHERE pid IN ({DEFAULT_PID}, {_SECOND_PID}) ORDER BY pid",
            )
        )
        assert [r.pid for r in rows] == sorted([DEFAULT_PID, _SECOND_PID]), (
            f"expected one process row per pid; got {[r.pid for r in rows]}"
        )

    def test_process_track_rows_still_present_after_ranking(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """Regression guard: the process track rows (one per pid) must
        still be present in the ``track`` table after the new fields
        are added to the descriptor. The rank field is not asserted
        here (it is a UI concern); the existence of the rows is the
        contract.
        """
        rows = list(
            trace_processor.query(
                "SELECT name FROM track WHERE name LIKE 'Process %' ORDER BY name",
            )
        )
        assert [r.name for r in rows] == sorted(
            [
                f"Process {DEFAULT_PID}",
                f"Process {_SECOND_PID}",
            ]
        ), f"expected process track rows for both pids; got {[r.name for r in rows]}"

    def test_process_track_order_matches_rank(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """The trace processor must order process tracks by
        ``sibling_order_rank`` when ``process_ordering=EXPLICIT`` is
        set on the root descriptor. The fixture emits ``_SECOND_PID``
        with an earlier first event ts (``_TS_START - 2_000_000``) and
        ``DEFAULT_PID`` with a later one (``_TS_START - 1_000_000``), so
        ``_SECOND_PID`` is expected to be ranked first (rank 0).

        The track table is what the Perfetto UI uses to render tracks;
        the track with the lower ``id`` appears first in the UI. We
        therefore assert that the row for ``_SECOND_PID`` has a lower
        track id than the row for ``DEFAULT_PID`` in the
        ``process_track_event`` rows.
        """
        rows = list(
            trace_processor.query(
                f"""
            SELECT t.id, p.pid
            FROM track t
            JOIN process_track pt ON t.id = pt.id
            JOIN process p ON pt.upid = p.upid
            WHERE t.type = 'process_track_event'
              AND p.pid IN ({DEFAULT_PID}, {_SECOND_PID})
        """
            )
        )
        pid_to_id = {r.pid: r.id for r in rows}
        assert DEFAULT_PID in pid_to_id
        assert _SECOND_PID in pid_to_id
        assert pid_to_id[_SECOND_PID] < pid_to_id[DEFAULT_PID], (
            f"expected _SECOND_PID (earlier first event) to have lower track id; got pid_to_id={pid_to_id}"
        )

    def test_process_table_start_ts_matches_first_event(
        self,
        trace_processor: TraceProcessor,
    ) -> None:
        """The ``process.start_ts`` column in the trace processor's
        SQL table must reflect the first non-meta event timestamp
        for each pid (i.e. the ``start_timestamp_ns`` written to the
        ``ProcessDescriptor`` by the encoder).

        The fixture emits ``DEFAULT_PID`` with its first event at
        ``_TS_START - 1_000_000`` and ``_SECOND_PID`` at
        ``_TS_START - 2_000_000`` (earlier).
        """
        rows = list(
            trace_processor.query(
                f"""
            SELECT pid, start_ts
            FROM process
            WHERE pid IN ({DEFAULT_PID}, {_SECOND_PID})
            ORDER BY pid
        """
            )
        )
        pid_to_start_ts = {r.pid: r.start_ts for r in rows}
        assert pid_to_start_ts == {
            DEFAULT_PID: _TS_START - 1_000_000,
            _SECOND_PID: _TS_START - 2_000_000,
        }, f"unexpected start_ts values: {pid_to_start_ts}"


_RSS_PID_1: int = DEFAULT_PID
_RSS_PID_2: int = _SECOND_PID
_RSS_VAL_1: int = 4_194_304  # 4 MB
_RSS_VAL_2: int = 8_388_608  # 8 MB
_RSS_VAL_3: int = 2_097_152  # 2 MB
_RSS_TS_1: int = 500_000_000
_RSS_TS_2: int = 1_500_000_000
_RSS_TS_3: int = 2_500_000_000


def _write_trace_with_rss(tmp: Path) -> Path:
    path = tmp / "trace_with_rss.pb"
    exporter: PerfettoExporter = PerfettoExporter(
        output_path=path,
        flush_threshold=1000,
        cmdline_provider=_fake_cmdline_provider,
    )
    exporter.add_rss_sample(_RSS_PID_1, _RSS_VAL_1, _RSS_TS_1)
    exporter.add_rss_sample(_RSS_PID_2, _RSS_VAL_2, _RSS_TS_2)
    exporter.add_rss_sample(_RSS_PID_1, _RSS_VAL_3, _RSS_TS_3)
    exporter.close()
    return path


@pytest.fixture
def trace_processor_with_rss(tmp_path: Path) -> Iterator[TraceProcessor]:
    path = _write_trace_with_rss(tmp_path)
    with open_trace_processor(path) as tp:
        yield tp


class TestRssCounterTrackIntegration:
    """Integration tests verifying RSS counter tracks are populated in
    Perfetto traces and queryable through the trace processor."""

    def test_rss_counter_track_present(
        self,
        trace_processor_with_rss: TraceProcessor,
    ) -> None:
        rows = list(trace_processor_with_rss.query("SELECT name FROM counter_track WHERE name = 'rss'"))
        assert len(rows) >= 1, "expected at least one 'rss' counter track"
        for r in rows:
            assert r.name == "rss"

    def test_rss_counter_values_match(
        self,
        trace_processor_with_rss: TraceProcessor,
    ) -> None:
        """Values written via ``add_rss_sample`` must appear in the
        ``counter`` table with the correct timestamp and value."""
        for expected_ts, expected_val in (
            (_RSS_TS_1, _RSS_VAL_1),
            (_RSS_TS_3, _RSS_VAL_3),
        ):
            rows = list(
                trace_processor_with_rss.query(
                    f"SELECT c.value, c.ts FROM counter c "
                    f"JOIN counter_track ct ON c.track_id = ct.id "
                    f"WHERE ct.name = 'rss' AND c.ts = {expected_ts}"
                )
            )
            matching = [r for r in rows if abs(r.value - expected_val) < 1]
            assert matching, (
                f"no counter row found for ts={expected_ts} val={expected_val}; got {[(r.ts, r.value) for r in rows]}"
            )

    def test_rss_counter_outside_gc_metrics_group(
        self,
        trace_processor_with_rss: TraceProcessor,
    ) -> None:
        """RSS counter track must NOT be parented inside a ``GC Metrics``
        group; it should be a top-level counter. Since the trace processor
        may not surface ``parent_id`` for OS-scoped parent relationships,
        verify by checking there is no ``GC Metrics`` track in the trace."""
        gc_metrics_rows = list(trace_processor_with_rss.query("SELECT name FROM track WHERE name = 'GC Metrics'"))
        assert not gc_metrics_rows, f"GC Metrics track should NOT appear in an RSS-only trace; got {gc_metrics_rows}"

    def test_rss_counter_tracks_per_pid(
        self,
        trace_processor_with_rss: TraceProcessor,
    ) -> None:
        """Each PID gets its own RSS counter track. Verify by counting
        distinct RSS counter track ids and total counter values."""
        # Two distinct RSS counter tracks (one per PID).
        rss_track_ids = list(
            trace_processor_with_rss.query("SELECT DISTINCT id FROM counter_track WHERE name = 'rss' ORDER BY id")
        )
        assert len(rss_track_ids) == 2, (
            f"expected 2 distinct RSS counter track ids (one per PID), got {len(rss_track_ids)}"
        )

        # Total counter values should be 3: PID 1 has 2 samples,
        # PID 2 has 1 sample.
        total_values = list(
            trace_processor_with_rss.query(
                "SELECT COUNT(*) AS cnt FROM counter c "
                "JOIN counter_track ct ON c.track_id = ct.id "
                "WHERE ct.name = 'rss'"
            )
        )
        assert total_values[0].cnt == 3, (
            f"expected 3 RSS counter values total (2 for PID 1, 1 for PID 2), got {total_values[0].cnt}"
        )

        # Both expected PIDs appear in the process table.
        for pid in (_RSS_PID_1, _RSS_PID_2):
            proc_rows = list(trace_processor_with_rss.query(f"SELECT pid FROM process WHERE pid = {pid}"))
            assert len(proc_rows) == 1, f"expected process row for PID {pid}"

    def test_rss_counter_track_name_and_unit(
        self,
        trace_processor_with_rss: TraceProcessor,
    ) -> None:
        """The RSS counter track is named ``rss``. Its unit column comes
        back as ``None`` or ``''``: gcmon sets no explicit unit."""
        rows = list(trace_processor_with_rss.query("SELECT name, unit FROM counter_track WHERE name = 'rss'"))
        assert len(rows) >= 1
        for r in rows:
            assert r.name == "rss"

    def test_rss_does_not_affect_gc_counters(
        self,
        tmp_path: Path,
    ) -> None:
        """Adding RSS samples must not remove or alter existing GC counter
        tracks: writing both GC events and RSS samples preserves GC tracks."""
        path = tmp_path / "trace_combined.pb"
        exporter = PerfettoExporter(output_path=path, flush_threshold=1000)
        exporter.add_event(
            DEFAULT_PID,
            create_mock_stats_item(
                gen=0,
                iid=0,
                collections=_COLLECTIONS,
                collected=_COLLECTED,
                uncollectable=_UNCOLLECTABLE,
                candidates=_CANDIDATES,
                heap_size=_HEAP_SIZE,
            ),
        )
        exporter.add_rss_sample(DEFAULT_PID, _RSS_VAL_1, _RSS_TS_1)
        exporter.close()

        with open_trace_processor(path) as tp:
            counter_tracks = {r.name.strip() for r in tp.query("SELECT name FROM counter_track")}
            # GC counter tracks should still be present.
            for expected in ("G0 collected", "G0 candidates", "Thread 0 heap_size"):
                assert expected in counter_tracks, (
                    f"GC counter track {expected!r} missing after adding RSS; got {sorted(counter_tracks)}"
                )
            assert "rss" in counter_tracks, (
                f"RSS counter track missing after adding RSS + GC events; got {sorted(counter_tracks)}"
            )


class TestTwoInterpretersHeapSizes:
    """Two interpreters in one process draw two `heap_size` rows.

    Both parent to the process track, so unqualified they would be siblings
    sharing a name: one row apparently drawn twice, and a PerfettoSQL query
    matching on it selecting both heaps at once.
    """

    @pytest.fixture(scope="class")
    def two_interpreters(self, tmp_path_factory: pytest.TempPathFactory) -> Iterator[TraceProcessor]:
        path = tmp_path_factory.mktemp("two_iids") / "trace.pb"
        exporter = PerfettoExporter(output_path=path, flush_threshold=1000)
        for iid, heap_size in ((0, 1_000), (1, 9_000)):
            exporter.add_event(
                DEFAULT_PID,
                create_mock_stats_item(gen=0, iid=iid, heap_size=heap_size, ts_start=_TS_START, ts_stop=_TS_STOP),
            )
        exporter.add_rss_sample(DEFAULT_PID, _RSS_VAL_1, _RSS_TS_1)
        exporter.close()
        with open_trace_processor(path) as tp:
            yield tp

    def test_each_interpreter_gets_a_row_of_its_own(self, two_interpreters: TraceProcessor) -> None:
        names = {r.name.strip() for r in two_interpreters.query("SELECT name FROM counter_track")}
        assert {"Thread 0 heap_size", "Thread 1 heap_size"} <= names
        assert "heap_size" not in names

    def test_a_query_can_select_one_heap(self, two_interpreters: TraceProcessor) -> None:
        rows = list(
            two_interpreters.query(
                "SELECT c.value AS value FROM counter c "
                "JOIN counter_track ct ON c.track_id = ct.id "
                "WHERE ct.name = 'Thread 1 heap_size'"
            )
        )
        assert [r.value for r in rows] == [9_000]

    def test_both_rows_parent_to_the_process_track(self, two_interpreters: TraceProcessor) -> None:
        parents = {
            r.name.strip(): r.parent_id
            for r in two_interpreters.query("SELECT name, parent_id FROM counter_track WHERE name LIKE '%heap_size'")
        }
        assert len(parents) == 2
        assert len(set(parents.values())) == 1

    def test_rss_stays_bare(self, two_interpreters: TraceProcessor) -> None:
        """Its owner is the process, and a process holds one."""
        names = {r.name.strip() for r in two_interpreters.query("SELECT name FROM counter_track")}
        assert "rss" in names

    def test_every_other_counter_name_is_what_it_was(self, two_interpreters: TraceProcessor) -> None:
        names = {r.name.strip() for r in two_interpreters.query("SELECT name FROM counter_track")}
        assert {"G0 collected", "G0 candidates", "G0 duration"} <= names


# Timestamps for the reused-pid trace, in ns. DEFAULT_PID is handed out
# twice: a first process that collects once and is then missing from a
# tick, and a second that collects once and answers two more. Both
# collections land inside the run, so neither span owes anything to a
# record predating the poll that read it.
_REUSE_TICKS: tuple[int, ...] = (200_000_000, 500_000_000, 900_000_000, 1_000_000_000, 1_100_000_000)
_REUSE_FIRST_GC: tuple[int, int] = (300_000_000, 400_000_000)
_REUSE_SECOND_GC: tuple[int, int] = (700_000_000, 800_000_000)
_REUSE_FIRST_SPAN: tuple[int, int] = (_REUSE_TICKS[0], _REUSE_FIRST_GC[1])
_REUSE_SECOND_SPAN: tuple[int, int] = (_REUSE_SECOND_GC[0], _REUSE_TICKS[3])


# What each process on the recycled pid was running. A provider answers
# whatever holds the pid when it is asked, so two processes on one pid give
# two answers.
_REUSE_CMDLINES: tuple[tuple[str, ...], ...] = (
    ("python3", "-m", "first_target"),
    ("python3", "-m", "second_target"),
)


def _reused_pid_cmdline_provider() -> Callable[[int], list[str] | None]:
    """Hand out ``_REUSE_CMDLINES`` in order for ``DEFAULT_PID``, one per
    read, and nothing for any other pid."""
    remaining = list(_REUSE_CMDLINES)

    def provider(pid: int) -> list[str] | None:
        if pid != DEFAULT_PID or not remaining:
            return None
        return list(remaining.pop(0))

    return provider


def _write_reused_pid_trace(tmp: Path) -> Path:
    """Write a Perfetto trace over a run in which the operating system
    handed ``DEFAULT_PID`` to a second process.

    Events go in ahead of the tick that reports them, which is the order
    the monitor produces: the poll phase runs first and the liveness call
    closes the tick. ``_SECOND_PID`` answers every tick but the ones that
    drop ``DEFAULT_PID``, so it brackets both processes.
    """
    path = tmp / "reused_pid.pb"
    exporter = PerfettoExporter(
        output_path=path,
        flush_threshold=1000,
        cmdline_provider=_reused_pid_cmdline_provider(),
    )
    exporter.add_process_liveness({DEFAULT_PID, _SECOND_PID}, _REUSE_TICKS[0])
    exporter.add_event(
        DEFAULT_PID,
        create_mock_stats_item(gen=_GEN, iid=_IID, ts_start=_REUSE_FIRST_GC[0], ts_stop=_REUSE_FIRST_GC[1]),
    )
    exporter.add_process_liveness({_SECOND_PID}, _REUSE_TICKS[1])
    exporter.add_event(
        DEFAULT_PID,
        create_mock_stats_item(gen=_GEN, iid=_IID, ts_start=_REUSE_SECOND_GC[0], ts_stop=_REUSE_SECOND_GC[1]),
    )
    exporter.add_process_liveness({DEFAULT_PID, _SECOND_PID}, _REUSE_TICKS[2])
    exporter.add_process_liveness({DEFAULT_PID, _SECOND_PID}, _REUSE_TICKS[3])
    exporter.add_process_liveness({_SECOND_PID}, _REUSE_TICKS[4])
    exporter.close()
    return path


@pytest.fixture
def reused_pid_trace_processor(tmp_path: Path) -> Iterator[TraceProcessor]:
    path = _write_reused_pid_trace(tmp_path)
    with open_trace_processor(path) as tp:
        yield tp


def _lifetime_spans(tp: TraceProcessor, name: str) -> list[tuple[int, int]]:
    """The ``Processes``-track slices named *name*, as drawn, earliest
    first."""
    rows = list(
        tp.query(
            f"SELECT s.ts AS ts, s.dur AS dur FROM slice s "
            f"JOIN track t ON s.track_id = t.id "
            f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' AND s.name = '{name}' "
            f"ORDER BY s.ts"
        )
    )
    return [(r.ts, r.ts + r.dur) for r in rows]


class TestReusedPidSpans:
    """A pid the operating system handed out twice draws a span per
    process, each bounding only the process it belongs to and named for
    which process it was. See ADR-0011.
    """

    def test_no_misplaced_end_events(self, reused_pid_trace_processor: TraceProcessor) -> None:
        """Two spans sharing a name have to pair up: a named END matches
        the BEGIN carrying that name, and there are two of those."""
        assert _misplaced_end_events(reused_pid_trace_processor) == 0

    def test_a_reused_pid_draws_a_span_per_process(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """Before this, the accumulator folded both processes into one
        min/max and drew a single span from the first observation of the
        first to the last of the second."""
        assert _lifetime_spans(reused_pid_trace_processor, f"Process {DEFAULT_PID}") == [_REUSE_FIRST_SPAN]
        assert _lifetime_spans(reused_pid_trace_processor, f"Process {DEFAULT_PID}#2") == [_REUSE_SECOND_SPAN]

    def test_neither_span_covers_the_gap_between_the_processes(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """The tick that dropped the pid is the one instant the run
        proves neither process was running. A single span would have
        covered it, and an operator zooming there would read a process
        with no events and no counters."""
        spans = _lifetime_spans(reused_pid_trace_processor, f"Process {DEFAULT_PID}") + _lifetime_spans(
            reused_pid_trace_processor, f"Process {DEFAULT_PID}#2"
        )
        gap_tick = _REUSE_TICKS[1]
        assert all(not start <= gap_tick <= end for start, end in spans)

    def test_each_span_records_its_own_observed_bounds(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """``real_start_ts`` and ``real_end_ts`` are the truth where a
        clip moved the drawn ones, so they have to be per process too."""
        rows = list(
            reused_pid_trace_processor.query(
                f"SELECT s.ts AS ts, a.flat_key AS flat_key, a.int_value AS int_value FROM args a "
                f"JOIN slice s ON s.arg_set_id = a.arg_set_id "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' "
                f"AND s.name IN ('Process {DEFAULT_PID}', 'Process {DEFAULT_PID}#2') "
                f"AND a.flat_key IN ('debug.real_start_ts', 'debug.real_end_ts') "
                f"ORDER BY s.ts, a.flat_key"
            )
        )
        observed: dict[int, dict[str, int]] = {}
        for row in rows:
            observed.setdefault(row.ts, {})[row.flat_key] = row.int_value
        assert observed == {
            _REUSE_FIRST_SPAN[0]: {
                "debug.real_start_ts": _REUSE_FIRST_SPAN[0],
                "debug.real_end_ts": _REUSE_FIRST_SPAN[1],
            },
            _REUSE_SECOND_SPAN[0]: {
                "debug.real_start_ts": _REUSE_SECOND_SPAN[0],
                "debug.real_end_ts": _REUSE_SECOND_SPAN[1],
            },
        }

    def test_the_second_process_is_named_apart(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """What the operator reads, and the same suffix the `--stats`
        table prints for the same run. The first process on a pid stays
        plain, so a run with no reuse reads as it always has."""
        rows = list(
            reused_pid_trace_processor.query(
                f"SELECT s.name AS name, COUNT(*) AS n FROM slice s "
                f"JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' GROUP BY s.name"
            )
        )
        assert {r.name: r.n for r in rows} == {
            f"Process {DEFAULT_PID}": 1,
            f"Process {DEFAULT_PID}#2": 1,
            f"Process {_SECOND_PID}": 1,
        }

    def test_a_query_reads_the_epoch_without_parsing_a_name(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """The suffix is for an operator; the annotation is for SQL. It
        is on every slice, the first process on a pid included, so a
        query filters on a number rather than inferring one from a suffix
        that is not there."""
        rows = list(
            reused_pid_trace_processor.query(
                f"SELECT s.ts AS ts, EXTRACT_ARG(s.arg_set_id, 'debug.pid_epoch') AS pid_epoch "
                f"FROM slice s JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' "
                f"ORDER BY s.ts, pid_epoch"
            )
        )
        assert [(r.ts, r.pid_epoch) for r in rows] == [
            (_REUSE_TICKS[0], 1),
            (_REUSE_TICKS[0], 1),
            (_REUSE_SECOND_SPAN[0], 2),
        ]

    def test_a_pid_nobody_reused_still_draws_one_span(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """``_SECOND_PID`` ran the whole time, so it reads exactly as it
        did before spans were keyed per process."""
        assert _lifetime_spans(reused_pid_trace_processor, f"Process {_SECOND_PID}") == [
            (_REUSE_TICKS[0], _REUSE_TICKS[-1])
        ]


class TestAProcessGroupPerProcess:
    """A pid held twice draws a process group per process, and everything
    under one belongs to the process it names.

    Before this there was one group for both. Its thread row carried both
    processes' pauses in one line of slices, its counters stepped from one
    process's values to the other's with nothing marking where, and its
    start timestamp was the first process's. See ADR-0011 and spec 0066.
    """

    def _processes(self, tp: TraceProcessor) -> list[tuple[int, int, str]]:
        """``(upid, start_ts, name)`` for every process on the reused pid,
        earliest first."""
        rows = list(
            tp.query(
                f"SELECT upid, start_ts, name FROM process WHERE pid = {DEFAULT_PID} ORDER BY start_ts",
            )
        )
        return [(r.upid, r.start_ts, r.name) for r in rows]

    def test_each_process_gets_its_own_upid(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """Two descriptors carrying one pid do not collapse, so a
        per-process total is a ``GROUP BY upid`` rather than a join
        through the ``Processes`` track."""
        processes = self._processes(reused_pid_trace_processor)

        assert len(processes) == 2
        assert len({upid for upid, _start, _name in processes}) == 2

    def test_each_group_is_stamped_where_its_process_started(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        starts = [start for _upid, start, _name in self._processes(reused_pid_trace_processor)]

        assert starts == [_REUSE_FIRST_SPAN[0], _REUSE_SECOND_SPAN[0]]

    def test_each_group_is_named_for_its_process(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """The same strings the two spans on the ``Processes`` track
        carry, so an operator matches a group to a span by eye."""
        names = [name for _upid, _start, name in self._processes(reused_pid_trace_processor)]

        assert names == [f"Process {DEFAULT_PID}", f"Process {DEFAULT_PID}#2"]

    def test_each_process_gets_its_own_thread_row(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """Two thread descriptors carrying one pid and one tid, kept apart
        by the group each hangs off."""
        rows = list(
            reused_pid_trace_processor.query(
                f"SELECT utid, upid FROM thread WHERE tid = {DEFAULT_PID}",
            )
        )

        assert len(rows) == 2
        assert len({r.upid for r in rows}) == 2

    def test_each_process_draws_its_own_pauses(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """One line of slices per process, rather than both processes'
        pauses interleaved on one."""
        rows = list(
            reused_pid_trace_processor.query(
                f"SELECT s.ts AS ts, th.upid AS upid FROM slice s "
                f"JOIN thread_track tt ON s.track_id = tt.id "
                f"JOIN thread th ON tt.utid = th.utid "
                f"WHERE s.name = '{_PAUSE_NAME}' AND th.tid = {DEFAULT_PID} ORDER BY s.ts"
            )
        )

        assert [r.ts for r in rows] == [_REUSE_FIRST_GC[0], _REUSE_SECOND_GC[0]]
        assert len({r.upid for r in rows}) == 2

    def test_each_process_gets_its_own_counter_rows(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """A step in a counter is a fact about a process now, not an
        artifact of where one ended and the next began."""
        rows = list(
            reused_pid_trace_processor.query(
                f"SELECT pt.upid AS upid FROM counter_track ct "
                f"JOIN process_track pt ON ct.parent_id = pt.id "
                f"JOIN process p ON pt.upid = p.upid "
                f"WHERE ct.name = 'G0 collected' AND p.pid = {DEFAULT_PID}"
            )
        )

        assert len(rows) == 2
        assert len({r.upid for r in rows}) == 2

    def test_each_process_gets_its_own_start_marker(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """The marker keeps a group's description visible, so a group
        without one is a row the UI hides."""
        rows = list(
            reused_pid_trace_processor.query(
                f"SELECT s.ts AS ts, pt.upid AS upid FROM slice s "
                f"JOIN process_track pt ON s.track_id = pt.id "
                f"JOIN process p ON pt.upid = p.upid "
                f"WHERE s.name = '{_START_PROCESS_MARKER_NAME}' AND p.pid = {DEFAULT_PID} ORDER BY s.ts"
            )
        )

        assert [r.ts for r in rows] == [_REUSE_FIRST_GC[0], _REUSE_SECOND_GC[0]]
        assert len({r.upid for r in rows}) == 2


class TestACommandLinePerProcess:
    """Each row names the program the process it draws was running.

    gcmon read a pid's command line once per trace, during the first
    process's life, and put that string on every row and every span
    carrying the pid. Where the pid went to a different program, the
    ``#2`` row named one that process never ran and nothing in the trace
    said so. See ADR-0010 and spec 0066.
    """

    def _expected(self) -> dict[str, str]:
        return {
            f"Process {DEFAULT_PID}": " ".join(_REUSE_CMDLINES[0]),
            f"Process {DEFAULT_PID}#2": " ".join(_REUSE_CMDLINES[1]),
        }

    def _span_cmdlines(self, tp: TraceProcessor, name_pattern: str) -> dict[str, str | None]:
        rows = list(
            tp.query(
                f"SELECT s.name AS name, EXTRACT_ARG(s.arg_set_id, 'debug.cmdline') AS cmdline "
                f"FROM slice s JOIN track t ON s.track_id = t.id "
                f"WHERE t.name = '{_PROCESS_LIFETIME_TRACK_NAME}' AND s.name LIKE '{name_pattern}'"
            )
        )
        return {r.name: r.cmdline for r in rows}

    def test_each_span_names_the_program_its_process_ran(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        assert self._span_cmdlines(reused_pid_trace_processor, f"Process {DEFAULT_PID}%") == self._expected()

    def test_each_group_names_the_program_its_process_ran(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """``description`` on the process track, which is the only place
        the trace processor surfaces a command line in SQL (ADR-0010)."""
        rows = list(
            reused_pid_trace_processor.query(
                f"SELECT p.name AS name, a.string_value AS description FROM process p "
                f"JOIN process_track pt ON pt.upid = p.upid "
                f"LEFT JOIN args a ON a.arg_set_id = pt.source_arg_set_id AND a.key = 'description' "
                f"WHERE p.pid = {DEFAULT_PID} AND pt.name = p.name"
            )
        )

        assert {r.name: r.description for r in rows} == self._expected()

    def test_a_process_gcmon_never_read_carries_none(
        self,
        reused_pid_trace_processor: TraceProcessor,
    ) -> None:
        """``_SECOND_PID`` was only ever reported live, so nothing asked
        what it was running. Absent beats wrong, and it is now the only
        way to get absent."""
        assert self._span_cmdlines(reused_pid_trace_processor, f"Process {_SECOND_PID}") == {
            f"Process {_SECOND_PID}": None
        }

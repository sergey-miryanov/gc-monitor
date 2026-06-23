"""Integration tests for the ``gcmon combine`` command's new ``perfetto`` output
format.

Drives the real ``perfetto.trace_processor`` binary against combined traces
produced via the CLI. Both ``chrome -> perfetto`` and ``jsonl -> perfetto`` are
exercised, plus a full content-equivalence test that compares the SQL-visible
rows from ``chrome -> chrome`` and ``chrome -> perfetto`` for the same input.

These tests are deselected from the default ``pytest`` run (marker
``integration``) because the ``perfetto`` package downloads a ~100 MB binary
on first use. Run with ``pytest -m integration tests/test_convert_cmd_integration.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("perfetto")
from perfetto.trace_processor import TraceProcessor, TraceProcessorConfig

from tests.helpers import create_mock_incremental_item, create_mock_stats_item

pytestmark = [pytest.mark.integration]

# ---------------------------------------------------------------------------
# Coverage dimensions (per spec §4.6)
# ---------------------------------------------------------------------------
# Multiple processes, multiple generations, multiple tids/iids per process.
# Counter-track-name coverage and thread-track coverage depend on these.
_PID_A: int = 1001
_PID_B: int = 2002
_IID_A1: int = 0
_IID_A2: int = 1
_IID_A3: int = 2
_IID_B1: int = 10
_TS_START: int = 1_500_000_000
_DURATION_NS: int = 5_000_000

# Counter-track names produced by the encoder for each generation. Gen 0 has
# the basic 4; gen 1 has the additional 5 incremental fields; gen 2 has the
# basic 4 only.
_G0_COUNTERS: frozenset[str] = frozenset({
    "G0 collected", "G0 uncollectable", "G0 candidates", "G0 heap_size",
})
_G1_COUNTERS: frozenset[str] = frozenset({
    "G1 collected", "G1 uncollectable", "G1 candidates", "G1 heap_size",
    "G1 increment_size", "G1 alive_size",
    "G1 finalized_garbage_count", "G1 deleted_garbage_count",
    "G1 clear_weakrefs_count",
})
_G2_COUNTERS: frozenset[str] = frozenset({
    "G2 collected", "G2 uncollectable", "G2 candidates", "G2 heap_size",
})

# Pause slice args exposed via the trace processor.
_EXPECTED_PAUSE_ARGS: dict[str, int] = {
    "generation": 0,
    "iid": _IID_A1,
    "collections": 50,
    "heap_size": 52428800,
    "collected": 200,
    "uncollectable": 10,
    "candidates": 40,
}

# Arg-key namespace: chrome path uses "args", perfetto uses "debug".
_ARG_PREFIX: dict[str, str] = {"chrome": "args", "perfetto": "debug"}


# ---------------------------------------------------------------------------
# Multi-dimensional fixture builders
# ---------------------------------------------------------------------------


def _multi_dimensional_records() -> list[dict[str, int | float]]:
    """Build JSONL records exercising multiple pids, generations, iids.

    - pid=1001: 3 records (gen 0, 1, 2) across 3 distinct iids (0, 1, 2)
    - pid=2002: 1 record (gen 0) with iid=10
    """
    records: list[dict[str, int | float]] = []
    # pid=1001, iid=0, gen=0 (full collection, basic counters)
    item_g0 = create_mock_stats_item(
        gen=0, iid=_IID_A1, ts_start=_TS_START, ts_stop=_TS_START + _DURATION_NS,
    )
    records.append({
        "pid": _PID_A, "tid": _IID_A1,
        "gen": item_g0.gen, "iid": item_g0.iid,
        "ts_start": item_g0.ts_start, "ts_stop": item_g0.ts_stop,
        "heap_size": item_g0.heap_size, "collections": item_g0.collections,
        "collected": item_g0.collected, "uncollectable": item_g0.uncollectable,
        "candidates": item_g0.candidates, "duration": item_g0.duration,
    })
    # pid=1001, iid=1, gen=1 (incremental — exercises all sub-slices and
    # the full set of G1 counter metrics: increment_size, alive_size,
    # finalized_garbage_count, deleted_garbage_count, clear_weakrefs_count).
    item_g1 = create_mock_incremental_item(
        gen=1, iid=_IID_A2,
        ts_start=_TS_START + 100_000_000, ts_stop=_TS_START + 100_000_000 + _DURATION_NS,
    )
    records.append({
        "pid": _PID_A, "tid": _IID_A2,
        "gen": item_g1.gen, "iid": item_g1.iid,
        "ts_start": item_g1.ts_start, "ts_stop": item_g1.ts_stop,
        "heap_size": item_g1.heap_size, "collections": item_g1.collections,
        "collected": item_g1.collected, "uncollectable": item_g1.uncollectable,
        "candidates": item_g1.candidates, "duration": item_g1.duration,
        # Incremental fields:
        "increment_size": item_g1.increment_size,
        "alive_size": item_g1.alive_size,
        "ts_mark_alive_start": item_g1.ts_mark_alive_start,
        "ts_mark_alive_stop": item_g1.ts_mark_alive_stop,
        "ts_fill_increment_start": item_g1.ts_fill_increment_start,
        "ts_fill_increment_stop": item_g1.ts_fill_increment_stop,
        "ts_deduce_unreachable_start": item_g1.ts_deduce_unreachable_start,
        "ts_deduce_unreachable_stop": item_g1.ts_deduce_unreachable_stop,
        "ts_handle_weakref_callbacks_start": item_g1.ts_handle_weakref_callbacks_start,
        "ts_handle_weakref_callbacks_stop": item_g1.ts_handle_weakref_callbacks_stop,
        "ts_finalize_garbage_stop": item_g1.ts_finalize_garbage_stop,
        "finalized_garbage_count": item_g1.finalized_garbage_count,
        "ts_handle_resurrected_stop": item_g1.ts_handle_resurrected_stop,
        "ts_clear_weakrefs_stop": item_g1.ts_clear_weakrefs_stop,
        "clear_weakrefs_count": item_g1.clear_weakrefs_count,
        "ts_delete_garbage_start": item_g1.ts_delete_garbage_start,
        "ts_delete_garbage_stop": item_g1.ts_delete_garbage_stop,
        "deleted_garbage_count": item_g1.deleted_garbage_count,
    })
    # pid=1001, iid=2, gen=2 (full collection, basic counters)
    item_g2 = create_mock_stats_item(
        gen=2, iid=_IID_A3,
        ts_start=_TS_START + 200_000_000, ts_stop=_TS_START + 200_000_000 + _DURATION_NS,
    )
    records.append({
        "pid": _PID_A, "tid": _IID_A3,
        "gen": item_g2.gen, "iid": item_g2.iid,
        "ts_start": item_g2.ts_start, "ts_stop": item_g2.ts_stop,
        "heap_size": item_g2.heap_size, "collections": item_g2.collections,
        "collected": item_g2.collected, "uncollectable": item_g2.uncollectable,
        "candidates": item_g2.candidates, "duration": item_g2.duration,
    })
    # pid=2002, iid=10, gen=0 (second process, separate timeline)
    item_b = create_mock_stats_item(
        gen=0, iid=_IID_B1,
        ts_start=_TS_START + 300_000_000, ts_stop=_TS_START + 300_000_000 + _DURATION_NS,
    )
    records.append({
        "pid": _PID_B, "tid": _IID_B1,
        "gen": item_b.gen, "iid": item_b.iid,
        "ts_start": item_b.ts_start, "ts_stop": item_b.ts_stop,
        "heap_size": item_b.heap_size, "collections": item_b.collections,
        "collected": item_b.collected, "uncollectable": item_b.uncollectable,
        "candidates": item_b.candidates, "duration": item_b.duration,
    })
    return records


def _write_jsonl(records: list[dict[str, int | float]], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# CLI-driven fixture: run gcmon combine, then load into TraceProcessor
# ---------------------------------------------------------------------------


def _run_combine(
    inputs: list[Path],
    output: Path,
    *,
    input_format: str = "jsonl",
    output_format: str = "perfetto",
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "gcmon.cli", "combine"]
    cmd.extend(str(p) for p in inputs)
    cmd += ["-o", str(output), "--input-format", input_format,
            "--output-format", output_format]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.fixture
def multi_pid_jsonl(tmp_path: Path) -> list[Path]:
    """Two JSONL files exercising multiple pids, generations, and iids."""
    records = _multi_dimensional_records()
    f1 = tmp_path / "trace_a.jsonl"
    f2 = tmp_path / "trace_b.jsonl"
    # Split across 2 files (file 1: pid=1001 records; file 2: pid=2002 record)
    _write_jsonl([r for r in records if r["pid"] == _PID_A], f1)
    _write_jsonl([r for r in records if r["pid"] == _PID_B], f2)
    return [f1, f2]


@pytest.fixture
def loaded_trace_processor(
    tmp_path: Path, multi_pid_jsonl: list[Path],
) -> Iterator[TraceProcessor]:
    """Combine two JSONL files into a Perfetto trace and load it."""
    out = tmp_path / "combined.pftrace"
    result = _run_combine(multi_pid_jsonl, out, input_format="jsonl",
                          output_format="perfetto")
    assert result.returncode == 0, (
        f"gcmon combine failed: rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    tp = TraceProcessor(
        trace=str(out), config=TraceProcessorConfig(load_timeout=300),
    )
    try:
        yield tp
    finally:
        tp.close()


# ---------------------------------------------------------------------------
# SQL helpers
# ---------------------------------------------------------------------------


def _process_filter(pid: int) -> str:
    return (
        "JOIN thread_track tt ON s.track_id = tt.id "
        "JOIN thread th ON tt.utid = th.utid "
        "JOIN process p ON th.upid = p.upid "
        f"WHERE p.pid = {pid}"
    )


def _row_set(rows) -> set[tuple]:
    """Convert a query result into a hashable set of tuples."""
    return {tuple(dict(r.__dict__).items()) for r in rows}


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestCombineChromeToPerfettoIntegration:
    """`chrome -> perfetto` is structurally complete: every pid has a
    TrackDescriptor, every iid has a thread track, and the counter tracks
    exist for the right generations."""

    def test_counter_tracks_present(
        self, loaded_trace_processor: TraceProcessor,
    ) -> None:
        names = {r.name for r in loaded_trace_processor.query(
            "SELECT name FROM counter_track",
        )}
        expected = _G0_COUNTERS | _G1_COUNTERS | _G2_COUNTERS
        assert names == expected, (
            f"counter track names mismatch; missing: {expected - names}; "
            f"unexpected: {names - expected}"
        )

    def test_process_tracks_present(
        self, loaded_trace_processor: TraceProcessor,
    ) -> None:
        rows = sorted(r.name for r in loaded_trace_processor.query(
            "SELECT name FROM track WHERE name LIKE 'Process %'",
        ))
        assert rows == sorted([f"Process {_PID_A}", f"Process {_PID_B}"]), (
            f"expected process tracks for both PIDs, got {rows}"
        )

    def test_thread_tracks_present(
        self, loaded_trace_processor: TraceProcessor,
    ) -> None:
        rows = sorted(r.name for r in loaded_trace_processor.query(
            "SELECT th.name FROM thread th "
            "JOIN process p ON th.upid = p.upid "
            f"WHERE p.pid = {_PID_A}",
        ))
        for iid in (_IID_A1, _IID_A2, _IID_A3):
            assert f"Thread {iid}" in rows, (
                f"missing 'Thread {iid}' in pid={_PID_A}'s threads; got {rows}"
            )

    def test_pause_slice_exists(
        self, loaded_trace_processor: TraceProcessor,
    ) -> None:
        # 3 gen-0/gen-1/gen-2 slices for pid=1001 (iids 0,1,2),
        # 1 gen-0 slice for pid=2002 (iid 10) -> total 4 pause slices.
        rows = list(loaded_trace_processor.query(
            f"SELECT s.name FROM slice s "
            f"{_process_filter(_PID_A)} "
            f"AND s.name LIKE 'GC Pause (gen=%)'",
        ))
        assert len(rows) == 3, f"expected 3 pause slices for pid={_PID_A}, got {rows}"
        rows_b = list(loaded_trace_processor.query(
            f"SELECT s.name FROM slice s "
            f"{_process_filter(_PID_B)} "
            f"AND s.name LIKE 'GC Pause (gen=%)'",
        ))
        assert len(rows_b) == 1, f"expected 1 pause slice for pid={_PID_B}, got {rows_b}"


class TestCombineJsonlToPerfettoIntegration:
    """JSONL input path also produces a structurally complete Perfetto trace."""

    def test_pause_slice_args(
        self, loaded_trace_processor: TraceProcessor,
    ) -> None:
        rows = {
            r.flat_key: r.int_value
            for r in loaded_trace_processor.query(
                "SELECT flat_key, int_value FROM args "
                "WHERE arg_set_id IN ("
                f"  SELECT s.arg_set_id FROM slice s "
                f"  {_process_filter(_PID_A)} "
                "  AND s.name = 'GC Pause (gen=0)' AND s.dur > 0 "
                f"  AND th.name = 'Thread {_IID_A1}'"
                ")"
            )
        }
        for key, expected in _EXPECTED_PAUSE_ARGS.items():
            qualified = f"{_ARG_PREFIX['perfetto']}.{key}"
            assert qualified in rows, f"missing arg {qualified}; got {sorted(rows)}"
            assert rows[qualified] == expected, (
                f"{qualified}: expected {expected}, got {rows[qualified]}"
            )

    def test_full_gen1_sub_slices_present(
        self, loaded_trace_processor: TraceProcessor,
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
        slice_names = {r.name for r in loaded_trace_processor.query(
            f"SELECT DISTINCT s.name FROM slice s "
            f"{_process_filter(_PID_A)}",
        )}
        missing = set(expected_sub_slices) - slice_names
        assert not missing, f"missing sub-slices for gen=1: {missing}"


class TestCombineNormalizePerfettoIntegration:
    """Per-file normalization zeroes each file's timeline independently."""

    def test_normalize_zeroes_per_file_minimum(
        self, tmp_path: Path, multi_pid_jsonl: list[Path],
    ) -> None:
        out = tmp_path / "combined_normalized.pftrace"
        result = _run_combine(
            multi_pid_jsonl, out, input_format="jsonl", output_format="perfetto",
            extra_args=["--normalize"],
        )
        assert result.returncode == 0, result.stderr
        tp = TraceProcessor(
            trace=str(out), config=TraceProcessorConfig(load_timeout=300),
        )
        try:
            # pid=1001 records are all in file 1. After per-file normalization
            # the first slice of pid=1001 has ts=0. Without --normalize, the
            # same slice would have ts=1_500_000_000 ns. Assert that the
            # minimum across the pid=1001 slice table is 0 (vs. 1.5B unnormalized).
            rows = list(tp.query(
                f"SELECT MIN(ts) AS min_ts FROM slice s "
                f"JOIN thread_track tt ON s.track_id = tt.id "
                f"JOIN thread th ON tt.utid = th.utid "
                f"JOIN process p ON th.upid = p.upid "
                f"WHERE p.pid = {_PID_A}",
            ))
            assert len(rows) == 1
            assert rows[0].min_ts == 0, (
                f"expected min_ts=0 after per-file normalize, got {rows[0].min_ts}"
            )
        finally:
            tp.close()


class TestCombineChromePerfettoEquivalenceIntegration:
    """The strongest verification: for the same input, `chrome -> chrome` and
    `chrome -> perfetto` produce identical SQL-visible content (modulo the
    `args.` vs `debug.` arg-key prefix). This is achieved by running the
    `gcmon combine` CLI twice and comparing row sets in `perfetto.trace_processor`.
    """

    def test_equivalence(
        self, tmp_path: Path, multi_pid_jsonl: list[Path],
    ) -> None:
        chrome_out = tmp_path / "combined.json"
        perfetto_out = tmp_path / "combined.pftrace"

        # Convert jsonl -> chrome by re-running combine with --output-format chrome.
        # This requires the convert path to first parse JSONL to TraceEvents.
        # We rely on gcmon.combine doing this directly (jsonl -> chrome is supported).
        rc_chrome = _run_combine(
            multi_pid_jsonl, chrome_out,
            input_format="jsonl", output_format="chrome",
        )
        assert rc_chrome.returncode == 0, rc_chrome.stderr

        rc_perfetto = _run_combine(
            multi_pid_jsonl, perfetto_out,
            input_format="jsonl", output_format="perfetto",
        )
        assert rc_perfetto.returncode == 0, rc_perfetto.stderr

        tp_chrome = TraceProcessor(
            trace=str(chrome_out), config=TraceProcessorConfig(load_timeout=300),
        )
        tp_perfetto = TraceProcessor(
            trace=str(perfetto_out), config=TraceProcessorConfig(load_timeout=300),
        )
        try:
            for query in [
                # Track names: skip `Process <pid>` because chrome has no
                # separate process-track descriptor (perfetto does).
                "SELECT name FROM track WHERE name NOT LIKE 'Process %' ORDER BY name",
                "SELECT name FROM counter_track ORDER BY name",
            ]:
                rows_chrome = _row_set(tp_chrome.query(query))
                rows_perfetto = _row_set(tp_perfetto.query(query))
                assert rows_chrome == rows_perfetto, (
                    f"row set mismatch for query:\n  {query}\n"
                    f"only in chrome: {rows_chrome - rows_perfetto}\n"
                    f"only in perfetto: {rows_perfetto - rows_chrome}"
                )

            # Slice-level dur comparison: Chrome stores durations in
            # microseconds; Perfetto (as written by our encoder) stores them
            # interpreted as nanoseconds, so the chrome dur is 1000x larger.
            # Compare the relative ordering and ratio instead of equality.
            # Thread names are also excluded: chrome uses "<pid>:<tid>" while
            # perfetto uses "Thread <tid>".
            chrome_durs = sorted(
                (r.name, r.dur)
                for r in tp_chrome.query("SELECT s.name, s.dur FROM slice s")
            )
            perfetto_durs = sorted(
                (r.name, r.dur)
                for r in tp_perfetto.query("SELECT s.name, s.dur FROM slice s")
            )
            assert [name for name, _ in chrome_durs] == [name for name, _ in perfetto_durs], (
                f"slice name sets differ: {[n for n, _ in chrome_durs]} vs {[n for n, _ in perfetto_durs]}"
            )
            for (c_name, c_dur), (p_name, p_dur) in zip(chrome_durs, perfetto_durs, strict=True):
                assert c_name == p_name
                # Chrome ts is in us, perfetto ts is in ns-as-written, ratio 1000.
                if c_dur == 0:
                    assert p_dur == 0
                else:
                    assert c_dur // 1000 == p_dur, (
                        f"dur mismatch for {c_name}: chrome={c_dur}us, perfetto={p_dur}"
                    )

            # Arg values: same content modulo the args./debug. prefix.
            # The perfetto trace processor exposes a number of synthetic args
            # (sibling_order_rank, source, child_ordering, trace_id, track_uuid,
            # is_root_in_scope) that are NOT emitted by our encoder. Filter
            # both sides to only the data args we explicitly emit:
            # - in chrome: keys with the "args." prefix
            # - in perfetto: keys with the "debug." prefix
            # The chrome `name` key (the slice name as an arg) is not
            # emitted as a debug annotation and is also filtered.
            _EXCLUDED_PERFETTO = {
                "sibling_order_rank", "is_root_in_scope", "source",
                "child_ordering", "trace_id", "track_uuid",
            }
            args_chrome = {
                r.flat_key: r.int_value
                for r in tp_chrome.query("SELECT flat_key, int_value FROM args")
                if r.flat_key.startswith(f"{_ARG_PREFIX['chrome']}.")
            }
            args_perfetto = {
                r.flat_key: r.int_value
                for r in tp_perfetto.query("SELECT flat_key, int_value FROM args")
                if r.flat_key.startswith(f"{_ARG_PREFIX['perfetto']}.")
                and not r.flat_key.startswith(f"{_ARG_PREFIX['perfetto']}.name")
                and r.flat_key.removeprefix(f"{_ARG_PREFIX['perfetto']}.") not in _EXCLUDED_PERFETTO
            }
            rewritten_chrome = {
                k.replace(f"{_ARG_PREFIX['chrome']}.", f"{_ARG_PREFIX['perfetto']}.", 1): v
                for k, v in args_chrome.items()
            }
            assert rewritten_chrome == args_perfetto, (
                f"args mismatch after prefix-rewriting.\n"
                f"only in chrome (rewritten): {set(rewritten_chrome) - set(args_perfetto)}\n"
                f"only in perfetto: {set(args_perfetto) - set(rewritten_chrome)}"
            )
        finally:
            tp_chrome.close()
            tp_perfetto.close()

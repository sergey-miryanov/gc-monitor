"""Shared fixtures and helpers for CodSpeed benchmarks.

These helpers build realistic GC event payloads so the benchmarks exercise the
same pure-Python hot paths gcmon runs when ingesting and exporting garbage
collection data captured from a monitored process.
"""

from __future__ import annotations

from typing import Any

from gcmon.data import GCStatsInfo


def make_gc_event(i: int, *, pid: int = 12345, iid: int = 0, gen: int = 0) -> GCStatsInfo:
    """Build a fully-populated GCStatsInfo resembling a real GC pause record.

    All optional phase timestamps are filled in so the trace conversion and
    stats-recording paths take their most expensive branches.
    """
    base = 1_000_000_000 + i * 5_000_000
    return GCStatsInfo(
        gen=gen,
        iid=iid,
        ts_start=base,
        ts_stop=base + 4_000_000,
        heap_size=20_000 + i,
        collections=5 + i,
        collected=50 + i,
        uncollectable=i % 3,
        candidates=10 + i,
        duration=0.004,
        increment_size=1_024 + i,
        alive_size=2_048 + i,
        ts_mark_alive_start=base + 100,
        ts_mark_alive_stop=base + 900,
        ts_fill_increment_start=base + 1_000,
        ts_fill_increment_stop=base + 1_800,
        ts_deduce_unreachable_start=base + 2_000,
        ts_deduce_unreachable_stop=base + 2_800,
        ts_handle_weakref_callbacks_start=base + 3_000,
        ts_handle_weakref_callbacks_stop=base + 3_200,
        ts_finalize_garbage_stop=base + 3_400,
        finalized_garbage_count=i % 5,
        ts_handle_resurrected_stop=base + 3_600,
        ts_clear_weakrefs_stop=base + 3_800,
        clear_weakrefs_count=i % 4,
        ts_delete_garbage_start=base + 3_850,
        ts_delete_garbage_stop=base + 3_950,
        deleted_garbage_count=i % 6,
    )


def make_jsonl_record(i: int, *, pid: int = 12345, iid: int = 0, gen: int = 0) -> dict[str, Any]:
    """Build a JSONL record dict matching the on-disk gcmon export format."""
    base = 1_000_000_000 + i * 5_000_000
    return {
        "pid": pid,
        "tid": iid,
        "gen": gen,
        "iid": iid,
        "ts_start": base,
        "ts_stop": base + 4_000_000,
        "heap_size": 20_000 + i,
        "collections": 5 + i,
        "collected": 50 + i,
        "uncollectable": i % 3,
        "candidates": 10 + i,
        "duration": 0.004,
        "increment_size": 1_024 + i,
        "alive_size": 2_048 + i,
        "ts_mark_alive_start": base + 100,
        "ts_mark_alive_stop": base + 900,
        "ts_fill_increment_start": base + 1_000,
        "ts_fill_increment_stop": base + 1_800,
    }

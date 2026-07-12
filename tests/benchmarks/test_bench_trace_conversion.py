"""Benchmarks for converting GC stats into Trace Event objects.

Converting captured GC records into Chrome Trace / Perfetto events is the main
CPU cost of every ``gcmon convert`` and export. These benchmarks measure both
single-item conversion and bulk conversion of a whole capture.
"""

from __future__ import annotations

import pytest
from pytest_codspeed import BenchmarkFixture

from gcmon.exporters.trace_converter import (
    convert_item_to_trace_format,
    convert_to_trace_format,
)
from gcmon.protocol import TGCStatsInfo, TInstantMsg

from .conftest import make_gc_event

EVENT_COUNT = 5_000


@pytest.mark.benchmark
def test_convert_item_to_trace_format(benchmark: BenchmarkFixture) -> None:
    event = make_gc_event(0, gen=1)

    result = benchmark(convert_item_to_trace_format, 12345, event)
    assert len(result) > 0


@pytest.mark.benchmark
def test_convert_to_trace_format_single_pid(benchmark: BenchmarkFixture) -> None:
    items: dict[int, list[TGCStatsInfo | TInstantMsg]] = {
        12345: [make_gc_event(i, gen=i % 3) for i in range(EVENT_COUNT)]
    }

    result = benchmark(convert_to_trace_format, items)
    assert len(result) > EVENT_COUNT


@pytest.mark.benchmark
def test_convert_to_trace_format_many_pids(benchmark: BenchmarkFixture) -> None:
    items: dict[int, list[TGCStatsInfo | TInstantMsg]] = {}
    for i in range(EVENT_COUNT):
        pid = 1000 + (i % 16)
        iid = i % 4
        items.setdefault(pid, []).append(make_gc_event(i, pid=pid, iid=iid, gen=i % 3))

    result = benchmark(convert_to_trace_format, items)
    assert len(result) > EVENT_COUNT

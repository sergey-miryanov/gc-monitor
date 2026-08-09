"""Does the loss row survive being read back as a stack?

Slices on one Perfetto track are a stack: an END closes the most recently
opened slice. A poll's windows for one interpreter all open at the same
instant, so their BEGINs have to go out widest first or the first END closes
the wrong span. A trace built the other way **still parses and still
renders** — ADR-0015 records that the trace processor reports
``misplaced_end_event = 0`` and reads a crossing span as nested — so nothing
downstream flags it and a one-character slip in the sort key silently
reparents every loss span.

Default suite on purpose. ``addopts`` deselects ``fuzz`` and the CI job that
runs it is gated off ``main`` and ``release/*``, so
``test_perfetto_loss_track.py`` — the same claim against the real trace
processor — does not run where the regression would land.

This walks the shared converter's output, over records the monitor produced
from a lossy read, so the ordering under test is the one `_ingest` chose.
"""

from collections.abc import Sequence
from typing import override
from unittest.mock import patch

import pytest

from gcmon.data import GCStatsInfo, LossMsg
from gcmon.exporters.exporter import EventsExporter
from gcmon.exporters.trace_converter import convert_to_trace_format
from gcmon.monitor import EventsMonitor
from gcmon.poll_status import PollStatus
from gcmon.protocol import TGCStatsInfo, TInstantMsg, TItem, TLossMsg
from gcmon.stats import StreamingStats
from gcmon.target_process import ExternalProcess
from gcmon.trace_event import LOSS_TID_BASE, BeginEvent, EndEvent, TraceEvent, loss_tid
from tests.helpers import create_mock_stats_item
from tests.test_monitor_cursor import POLL_0, POLL_1, build_batch

PID = 12345
IID = 0

Slice = tuple[str, int, int, int]
"""``(name, ts_start, ts_stop, depth)``."""


class Recorder(EventsExporter):
    """Every record the monitor exported, in the order it exported them."""

    def __init__(self) -> None:
        super().__init__()
        self.items: list[TItem] = []

    @override
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        self.items.append(item)

    @override
    def add_loss_event(self, pid: int, item: TLossMsg) -> None:
        self.items.append(item)

    @override
    def add_instant_event(self, pid: int, item: TInstantMsg) -> None:
        self.items.append(item)

    @override
    def close(self) -> None:
        pass


def ingest(*batches: Sequence[GCStatsInfo]) -> list[TItem]:
    """Poll a monitor once per batch and hand back what it exported."""
    recorder = Recorder()
    monitor = EventsMonitor(ExternalProcess(pid=PID), recorder, StreamingStats())
    reads = iter(batches)

    def one_read(pid: int, all_interpreters: bool = True) -> list[GCStatsInfo]:
        return list(next(reads))

    with patch("gcmon.monitor.get_gc_stats", side_effect=one_read):
        for _ in batches:
            assert monitor.poll(PID) is PollStatus.OK

    return recorder.items


def loss_slices(items: Sequence[TItem]) -> dict[tuple[int, int], list[Slice]]:
    """Every loss row's slices, resolved the way a trace processor resolves
    them.

    Groups the converter's BEGIN/END events by ``(pid, tid)``, sorts each row
    by timestamp — **stably**, so events sharing one timestamp keep the order
    they were emitted in, which is the thing under test — and walks it as a
    stack. Fails if an END closes a slice other than the one that opened it,
    or if the row does not end empty.
    """
    events = convert_to_trace_format({PID: items})
    rows: dict[tuple[int, int], list[BeginEvent | EndEvent]] = {}
    for event in events:
        if isinstance(event, BeginEvent | EndEvent) and event.tid <= LOSS_TID_BASE:
            rows.setdefault((event.pid, event.tid), []).append(event)

    resolved: dict[tuple[int, int], list[Slice]] = {}
    for row, row_events in rows.items():
        stack: list[tuple[str, int, int]] = []
        slices: list[Slice] = []
        for event in sorted(row_events, key=lambda e: e.ts):
            if isinstance(event, BeginEvent):
                stack.append((event.name, event.ts, len(stack)))
                continue
            assert stack, f"{row}: END of {event.name!r} at {event.ts} with nothing open"
            name, ts_start, depth = stack.pop()
            assert name == event.name, f"{row}: END of {event.name!r} closed {name!r}, opened at {ts_start}"
            slices.append((name, ts_start, event.ts, depth))
        assert not stack, f"{row}: {[name for name, _ts, _d in stack]} left open"
        resolved[row] = sorted(slices, key=lambda s: (s[1], -s[2]))

    return resolved


def three_generations() -> tuple[list[GCStatsInfo], list[GCStatsInfo], list[GCStatsInfo]]:
    """Three polls, every generation losing records across each seam.

    Each generation's next observed record sits further out than the one below
    it, so the widths run gen 2 > gen 1 > gen 0 and the ``groupby`` order
    `_ingest` walks its keys in — 0, 1, 2 — is exactly narrowest first.
    """

    def batch(nth: int, counters: tuple[int, int, int], starts: tuple[int, int, int]) -> list[GCStatsInfo]:
        return [
            create_mock_stats_item(
                gen=gen,
                iid=IID,
                collections=counters[gen],
                ts_start=starts[gen],
                ts_stop=starts[gen] + 100_000,
                # Cumulative through this record: three collections per seam,
                # 100 us each, so every window carries 200 us of lost pause.
                duration=(1 + 3 * nth) * 100e-6,
            )
            for gen in (0, 1, 2)
        ]

    return (
        batch(0, (10, 20, 30), (1_000_000, 1_200_000, 1_400_000)),
        batch(1, (13, 23, 33), (5_000_000, 7_000_000, 9_000_000)),
        batch(2, (16, 26, 36), (12_000_000, 14_000_000, 16_000_000)),
    )


class TestThreeGenerationsOnOneRow:
    """The shape the ordering exists for: one poll blind in all three."""

    def row(self) -> list[Slice]:
        return loss_slices(ingest(*three_generations()))[(PID, loss_tid(IID))]

    def test_each_generation_gets_a_span_of_its_own(self) -> None:
        """One bar carrying three generations' counts said gcmon was blind
        here without saying which generation went blind, or for how long."""
        assert [name for name, _s, _e, _d in self.row()] == [
            "GC Loss(2)",
            "GC Loss(1)",
            "GC Loss(0)",
            "GC Loss(2)",
            "GC Loss(1)",
            "GC Loss(0)",
        ]

    def test_they_nest_outermost_first(self) -> None:
        """`groupby` hands `_ingest` its keys narrowest first, which would put
        gen 0's END on gen 2's span with nothing to say so."""
        assert [depth for _n, _s, _e, depth in self.row()] == [0, 1, 2, 0, 1, 2]

    def test_the_row_shares_one_left_edge_per_poll(self) -> None:
        """What makes nesting safe rather than a coincidence: a bulk read
        confirms every ring at once, so one poll can only open windows at one
        instant."""
        edges = [ts_start for _n, ts_start, _e, _d in self.row()]

        assert edges == [1_500_000] * 3 + [9_100_000] * 3

    def test_each_span_ends_at_its_own_generation_next_record(self) -> None:
        first_poll = [(name, ts_stop) for name, _s, ts_stop, _d in self.row()][:3]

        assert first_poll == [
            ("GC Loss(2)", 9_000_000),
            ("GC Loss(1)", 7_000_000),
            ("GC Loss(0)", 5_000_000),
        ]

    def test_spans_from_different_polls_do_not_nest(self) -> None:
        """A poll opens at or after the newest record the poll before it saw,
        which is at or after every window that poll closed."""
        row = self.row()

        assert row[3][1] > max(ts_stop for _n, _s, ts_stop, _d in row[:3])


class TestTheVerbatimCapture:
    """Real slot data, from the two-poll capture in `test_monitor_cursor`."""

    def row(self) -> list[Slice]:
        return loss_slices(ingest(build_batch(POLL_0), build_batch(POLL_1)))[(PID, loss_tid(IID))]

    def test_the_two_generations_nest(self) -> None:
        assert self.row() == [
            ("GC Loss(0)", 294787154918900, 294787244879600, 0),
            ("GC Loss(1)", 294787154918900, 294787228540400, 1),
        ]

    def test_the_counts_ride_on_the_generation_that_lost_them(self) -> None:
        args = [
            e.args
            for e in convert_to_trace_format({PID: ingest(build_batch(POLL_0), build_batch(POLL_1))})
            if isinstance(e, BeginEvent) and e.tid <= LOSS_TID_BASE
        ]

        assert [(a["generation"], a["lost_count"]) for a in args] == [(0, 76), (1, 5)]


class TestTheControls:
    def test_a_lossless_run_draws_no_loss_row(self) -> None:
        """The capture polled twice with the same batch: no counter moved, so
        nothing was lost and nothing is drawn."""
        batch = build_batch(POLL_0)

        assert loss_slices(ingest(batch, batch)) == {}

    def test_the_walk_rejects_a_crossing_row(self) -> None:
        """The check's own negative control. Two spans that cross come back
        from the trace processor as a clean nesting, so a walk that accepted
        them would pass in a world where the ordering did nothing."""
        crossing = [
            LossMsg(iid=IID, gen=0, ts_start=2_000, ts_stop=5_000, lost_count=1),
            LossMsg(iid=IID, gen=1, ts_start=4_000, ts_stop=9_000, lost_count=1),
        ]

        with pytest.raises(AssertionError, match="closed"):
            loss_slices(crossing)

    def test_the_walk_rejects_a_narrowest_first_row(self) -> None:
        """The order `_ingest` would emit with the sort reversed: same spans,
        same widths, one shared left edge, opened the wrong way round."""
        narrowest_first = [
            LossMsg(iid=IID, gen=0, ts_start=1_000, ts_stop=2_000, lost_count=1),
            LossMsg(iid=IID, gen=1, ts_start=1_000, ts_stop=9_000, lost_count=1),
        ]

        with pytest.raises(AssertionError, match="closed"):
            loss_slices(narrowest_first)


def test_the_events_are_what_the_walk_reads() -> None:
    """`loss_slices` is worth nothing if it walks an empty row. Pin that the
    converter really put BEGIN/END pairs on the loss tid."""
    events: list[TraceEvent] = convert_to_trace_format({PID: ingest(*three_generations())})
    on_the_row = [e for e in events if isinstance(e, BeginEvent | EndEvent) and e.tid == loss_tid(IID)]

    assert len(on_the_row) == 12

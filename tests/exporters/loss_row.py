"""A lossy run, and the loss row a trace processor would build from it.

Helpers, not tests. `ingest` polls a real `EventsMonitor` on a fixed clock and
returns what it exported; `loss_slices` walks the shared converter's output the
way a trace processor walks a track, as a stack.

The walk is the instrument the assertions rest on. Slices on one Perfetto track
are a stack, so an END closes the most recently opened slice, and a row whose
spans overlap **still parses and still renders**: ADR-0015 records that the
trace processor reports ``misplaced_end_event = 0`` and reads a crossing span
as nested. Resolving the row here is what makes the shape visible at all.
"""

from collections.abc import Iterator, Sequence
from typing import override
from unittest.mock import patch

from gcmon.data import GCStatsInfo
from gcmon.exporters.exporter import EventsExporter
from gcmon.exporters.trace_converter import convert_to_trace_format
from gcmon.monitor import EventsMonitor
from gcmon.poll_status import PollStatus
from gcmon.protocol import TGCStatsInfo, TInstantMsg, TItem, TLossMsg
from gcmon.stats import StreamingStats
from gcmon.target_process import ExternalProcess
from gcmon.trace_event import LOSS_TID_BASE, BeginEvent, EndEvent
from tests.helpers import create_mock_stats_item

PID = 12345
IID = 0

# What the monitor's clock reads at each poll. A span's edges are two of
# these, so pinning them is what lets a test name a timestamp at all.
POLL_TIMES = [1_000_000, 10_000_000, 20_000_000, 30_000_000]

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
    """Poll a monitor once per batch and hand back what it exported.

    Through ``poll`` rather than ``_ingest``, so the poll instants come from
    where they come from in production. The clock is fixed to ``POLL_TIMES``:
    every loss span's width is the distance between two reads, which a real
    monotonic clock would make unrepeatable.
    """
    recorder = Recorder()
    monitor = EventsMonitor(ExternalProcess(pid=PID), recorder, StreamingStats())
    reads = iter(batches)

    def one_read(pid: int, all_interpreters: bool = True) -> list[GCStatsInfo]:
        return list(next(reads))

    def clock() -> Iterator[int]:
        # Two calls per poll: `poll` brackets the read to time it.
        for instant in POLL_TIMES:
            yield instant
            yield instant + 1_000

    ticks = clock()

    with (
        patch("gcmon.monitor.get_gc_stats", side_effect=one_read),
        patch("gcmon.monitor.time.monotonic_ns", side_effect=lambda: next(ticks)),
    ):
        for _ in batches:
            assert monitor.poll(PID) is PollStatus.OK

    return recorder.items


def loss_slices(items: Sequence[TItem]) -> dict[tuple[int, int], list[Slice]]:
    """Every loss row's slices, resolved the way a trace processor resolves
    them.

    Groups the converter's BEGIN/END events by ``(pid, tid)``, sorts each row
    by timestamp — **stably**, so events sharing one timestamp keep the order
    they were emitted in, which is what a trace processor does with them — and
    walks it as a stack.

    Fails if a span opens while another is still open. Two spans on this row
    share a name whenever they lost the same generations, so a walk that only
    checked names against each other could pair an END with the wrong BEGIN and
    never notice. Flatness is the property anyway: the row claims a sequence of
    intervals, and an interval inside another one is the reading being designed
    out.
    """
    events = convert_to_trace_format({PID: items})
    rows: dict[tuple[int, int], list[BeginEvent | EndEvent]] = {}
    for event in events:
        if isinstance(event, BeginEvent | EndEvent) and event.tid <= LOSS_TID_BASE:
            rows.setdefault((event.pid, event.tid), []).append(event)

    resolved: dict[tuple[int, int], list[Slice]] = {}
    for row, row_events in rows.items():
        stack: list[tuple[str, int]] = []
        slices: list[Slice] = []
        for event in sorted(row_events, key=lambda e: e.ts):
            if isinstance(event, BeginEvent):
                assert not stack, f"{row}: {event.name!r} at {event.ts} opened inside {stack[-1][0]!r}"
                stack.append((event.name, event.ts))
                continue
            assert stack, f"{row}: END of {event.name!r} at {event.ts} with nothing open"
            name, ts_start = stack.pop()
            assert name == event.name, f"{row}: END of {event.name!r} closed {name!r}, opened at {ts_start}"
            slices.append((name, ts_start, event.ts, 0))
        assert not stack, f"{row}: {[name for name, _ts in stack]} left open"
        resolved[row] = sorted(slices, key=lambda s: (s[1], -s[2]))

    return resolved


def three_generations() -> tuple[list[GCStatsInfo], list[GCStatsInfo], list[GCStatsInfo]]:
    """Three polls, every generation losing records across each seam."""

    def batch(nth: int, counters: tuple[int, int, int], starts: tuple[int, int, int]) -> list[GCStatsInfo]:
        return [
            create_mock_stats_item(
                gen=gen,
                iid=IID,
                collections=counters[gen],
                ts_start=starts[gen],
                ts_stop=starts[gen] + 100_000,
                # Cumulative through this record: three collections per seam,
                # 100 us each, so every generation loses 200 us per interval.
                duration=(1 + 3 * nth) * 100e-6,
            )
            for gen in (0, 1, 2)
        ]

    return (
        batch(0, (10, 20, 30), (100_000, 200_000, 300_000)),
        batch(1, (13, 23, 33), (5_000_000, 6_000_000, 7_000_000)),
        batch(2, (16, 26, 36), (12_000_000, 14_000_000, 16_000_000)),
    )

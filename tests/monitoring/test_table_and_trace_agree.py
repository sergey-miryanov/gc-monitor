"""One run over a reused pid, read back through both of its outputs.

The epoch is counted twice: `StreamingStats` advances it when a pid leaves the
process tree, and the Perfetto encoder advances it when a pid drops out of a
liveness report. Two triggers, and they are not the same one -- a pid the
control server suppresses, or one whose read fails once, leaves the live set
while staying in the tree, and ADR-0011 records what that costs. What this
file covers is the case both fire on, a process exiting, which is every
handover gcmon reports.

The comparison is by interval, not by label. Both sides format their `#N`
through `epoch_suffix`, so matching the strings would pass while both counters
were wrong in the same direction. What is asserted instead is that the records
the trace draws *inside* a span are the ones the table filed under the block
of that name.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

import pytest
from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import TracePacket, TrackEvent

from gcmon.exporters.perfetto_exporter import PerfettoExporter
from gcmon.model.protocol import TGCStatsInfo
from gcmon.monitoring.monitor import EventsMonitor
from gcmon.monitoring.target_process import ExternalProcess
from gcmon.monitoring.wait_policy import no_wait_policy
from gcmon.stats.stats_output import _ring_label
from gcmon.stats.streaming_stats import StreamingStats
from gcmon.support.pid_epoch import epoch_suffix
from tests.helpers import FakeEventsReader, create_mock_stats_item, perfetto_packets

TARGET_PID = 5000
REUSED_PID = 6000
IID = 0

# The tick the child is missing from the listing: a process exiting, which is
# all gcmon ever sees of a pid being handed on. The listing is what settles the
# table's ring, and the liveness report the same tick leaves it out of is what
# closes the trace's span. One mechanism per output, both fired by this.
_HANDOVER_TICK = 3
_TICKS = 7
_TICK_NS = 500_000_000

# When each process collected. Far enough apart that no record of one could be
# mistaken for a record of the other, and each inside the stretch its own
# process was being polled over.
_FIRST_LIFE = (1_000_000_000, 1_200_000_000, 1_400_000_000)
_SECOND_LIFE = (2_200_000_000, 2_700_000_000)

_PAUSE_PREFIX = "GC Pause("
_SPAN_PREFIX = "Process "


def _record(collections: int, ts_start: int) -> TGCStatsInfo:
    """One finished collection. ``collections`` is what identifies a record to
    the monitor, and it counts up across both lives, so nothing here is a
    re-read of a slot already seen."""
    return create_mock_stats_item(
        gen=0,
        iid=IID,
        collections=collections,
        ts_start=ts_start,
        ts_stop=ts_start + 1_000_000,
    )


def _ring(tick: int) -> Sequence[TGCStatsInfo]:
    """What the reused pid's ring holds on *tick*.

    Every poll returns the whole ring and the monitor keeps a cursor into it,
    so only what is new reaches the exporter. The second process starts a ring
    of its own, which is why none of the first life is in it.
    """
    if tick < _HANDOVER_TICK:
        return [_record(n, ts) for n, ts in enumerate(_FIRST_LIFE[: tick + 1], start=1)]
    start = len(_FIRST_LIFE) + 1
    return [_record(n, ts) for n, ts in enumerate(_SECOND_LIFE[: tick - _HANDOVER_TICK], start=start)]


class Run:
    """A monitored run's two outputs, held together."""

    def __init__(self, trace: bytes, stats: StreamingStats) -> None:
        self.packets: list[TracePacket] = perfetto_packets(trace)
        self.stats = stats

    def _pid_by_track(self) -> dict[int, int]:
        by_track: dict[int, int] = {}
        for packet in self.packets:
            if not packet.HasField("track_descriptor"):
                continue
            descriptor = packet.track_descriptor
            if descriptor.HasField("thread"):
                by_track[descriptor.uuid] = descriptor.thread.pid
            elif descriptor.HasField("process"):
                by_track[descriptor.uuid] = descriptor.process.pid
        return by_track

    def pause_timestamps(self, pid: int) -> list[int]:
        """When each `GC Pause` slice the trace draws for *pid* began."""
        by_track = self._pid_by_track()
        return [
            packet.timestamp
            for packet in self.packets
            if packet.HasField("track_event")
            and packet.track_event.name.startswith(_PAUSE_PREFIX)
            and packet.track_event.type == TrackEvent.Type.TYPE_SLICE_BEGIN
            and by_track.get(packet.track_event.track_uuid) == pid
        ]

    def spans_by_name(self) -> dict[str, tuple[int, int]]:
        """Each `Processes` slice's observed interval, by the name it draws
        under. Read off `real_start_ts` / `real_end_ts`, which are the truth
        wherever a clip moved the drawn ones."""
        spans: dict[str, tuple[int, int]] = {}
        for packet in self.packets:
            if not packet.HasField("track_event"):
                continue
            event = packet.track_event
            if event.type != TrackEvent.Type.TYPE_SLICE_BEGIN or not event.name.startswith(_SPAN_PREFIX):
                continue
            observed = {a.name: a.int_value for a in event.debug_annotations}
            spans[event.name] = (observed["real_start_ts"], observed["real_end_ts"])
        return spans

    def sampled_pauses(self, pid: int, iid: int, pid_epoch: int) -> int:
        """How many collections the table's block for that ring counts."""
        return sum(self.stats.pause_totals(pid, iid, gen, pid_epoch).sampled_count for gen in StreamingStats.GENS)


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> Run:
    """Drive the real monitor over a pid the listing drops and hands back.

    A real `PerfettoExporter` and a real `StreamingStats`, because the two
    counters this compares live one in each.
    """
    path: Path = tmp_path_factory.mktemp("agree") / "gcmon.pftrace"
    stats = StreamingStats()
    exporter = PerfettoExporter(output_path=path, flush_threshold=1000, cmdline_provider=lambda pid: None)
    tick = 0
    monitor = EventsMonitor(
        ExternalProcess(pid=TARGET_PID),
        exporter,
        stats,
        reader=FakeEventsReader(lambda pid: [] if pid == TARGET_PID else _ring(tick)),
        wait_policy_factory=no_wait_policy,
    )
    tree: list[list[int]] = [[] if n == _HANDOVER_TICK else [REUSED_PID] for n in range(_TICKS)]
    listings = iter(tree)
    with patch(
        "gcmon.monitoring.monitor.get_child_pids",
        side_effect=lambda pid, recursive=True: next(listings),
    ):
        for tick in range(_TICKS):
            monitor.tick((tick + 1) * _TICK_NS, stop=lambda: False)
    monitor.stop()
    exporter.close()
    return Run(path.read_bytes(), stats)


class TestTheRunIsWorthComparing:
    """A cross-check over a run with no reuse in it would stay green whatever
    either counter did."""

    def test_the_table_files_the_pid_under_two_processes(self, run: Run) -> None:
        assert sorted(run.stats.rings()) == [(REUSED_PID, IID, 1), (REUSED_PID, IID, 2)]

    def test_the_trace_draws_the_pid_as_two_processes(self, run: Run) -> None:
        drawn = run.spans_by_name().keys()

        assert {f"Process {REUSED_PID}", f"Process {REUSED_PID}#2"} <= drawn

    def test_both_processes_collected(self, run: Run) -> None:
        """Neither block is empty, so a comparison counting zero on both sides
        cannot pass by default."""
        assert run.sampled_pauses(REUSED_PID, IID, 1) == len(_FIRST_LIFE)
        assert run.sampled_pauses(REUSED_PID, IID, 2) == len(_SECOND_LIFE)


class TestTheTableAndTheTraceAgree:
    """An operator who reads `6000:0#2` in the table and opens the trace of
    the same run finds `Process 6000#2` describing that same process."""

    @pytest.mark.parametrize("pid_epoch", [1, 2])
    def test_the_block_and_the_span_cover_the_same_records(self, run: Run, pid_epoch: int) -> None:
        """The span named for a process brackets exactly the collections the
        block of that name counts. Advance either counter one step out of line
        with the other and the records land on the wrong side of a boundary.
        """
        start, end = run.spans_by_name()[f"Process {REUSED_PID}{epoch_suffix(pid_epoch)}"]

        inside = [ts for ts in run.pause_timestamps(REUSED_PID) if start <= ts <= end]

        assert len(inside) == run.sampled_pauses(REUSED_PID, IID, pid_epoch)

    def test_every_collection_falls_inside_exactly_one_span(self, run: Run) -> None:
        """Nothing is left in the gap between the two processes and nothing is
        counted twice: the spans partition what the trace drew for the pid."""
        spans = [span for name, span in run.spans_by_name().items() if name.startswith(f"Process {REUSED_PID}")]

        counted = [sum(1 for start, end in spans if start <= ts <= end) for ts in run.pause_timestamps(REUSED_PID)]

        assert counted == [1] * (len(_FIRST_LIFE) + len(_SECOND_LIFE))

    @pytest.mark.parametrize("pid_epoch", [1, 2])
    def test_the_block_heading_and_the_span_name_carry_one_suffix(self, run: Run, pid_epoch: int) -> None:
        """The part an operator matches by eye. It is the weakest of these
        assertions -- one definition formats both -- so it is written down
        separately rather than folded into the ones above."""
        label = _ring_label(REUSED_PID, IID, pid_epoch)
        name = f"Process {REUSED_PID}{epoch_suffix(pid_epoch)}"

        assert label.removeprefix(f"{REUSED_PID}:{IID}") == name.removeprefix(f"Process {REUSED_PID}")
        assert name in run.spans_by_name()

"""A whole monitored run, pinned to the byte.

Everything else in the suite checks one piece of a tick. `test_loss_replay`
drives `EventsMonitor.poll` straight off the capture and never runs the loop;
`test_monitor_loop` runs the loop against a `MagicMock` monitor that produces no
trace at all. The seam between them -- discovery, prune, poll order, the policy
verdict, liveness, and the bytes that come out the other end -- has no witness,
which is exactly the seam spec 0038 is about to rearrange.

So this file runs the real `MonitorLoop` over the real `EventsMonitor` over the
real Chrome exporter, feeds it the `SSL_CONTEXT_SIZE` capture through a scripted
process tree and a scripted clock, and compares the trace to
`tests/fixtures/monitored_run_chrome_trace.json` byte for byte.

**This test exists to be broken deliberately.** A red run here means the trace an
operator opens is not the trace they opened yesterday. That is a fine thing to
do on purpose and a terrible thing to do by accident, so the workflow is: read
the diff, convince yourself every changed line is a change you meant, then
regenerate the fixture with

    PYTHONPATH=src python -m tests.monitoring.test_monitored_run_trace

and commit the new fixture *in the same commit as the change that moved it*, so
the diff is reviewable next to its cause. Never regenerate to make a red run go
away.

Determinism is the whole difficulty, and it comes down to four things.

*One clock, two consumers.* `monitor_loop` and `monitor` both do `import time`,
so they share one `time.monotonic_ns` and one patch feeds both. Per tick the
order is fixed: the loop reads once for the tick instant, then each polled pid
costs the monitor two reads, one either side of `get_gc_stats`. `_script`
lays that sequence out in advance and `_ScriptedClock` hands it out in order,
raising rather than inventing a value if the run asks for more than was written.
`test_the_clock_was_spent_exactly` then checks none was left over: an ordering
change that read fewer instants would otherwise shift every timestamp in the
trace and still pass.

*Nothing else may read the machine.* `NoWaitPolicy` in place of
`StartupTimeoutPolicy`, which reads `time.monotonic` in seconds; a fixed-tick
runner in place of `DurationRunner`, which reads it too; `rate=0` so the wait
between ticks returns at once; and no RSS sampler, which would sample this
machine's memory.

*The capture drives both pids.* The target replays `SSL_CONTEXT_SIZE` as it
stands. The child replays the same collections shifted `CHILD_SKEW_NS` later, so
its ring lands on different instants and its loss windows fall in different
places -- two pids with genuinely different per-pid state rather than one pid
counted twice.

*The Chrome leg only.* Perfetto's process-lifetime sweep clips spans by the
order liveness observations arrive in, which the loop's timing feeds; Chrome
drops liveness on the floor (`add_process_liveness` is a base-class no-op) and
resolves no cmdline through psutil, so its bytes depend on nothing but the
capture and the script.

The fixture is stored as the encoder's own output, unmodified. That format is
already one JSON object per line, so the stored bytes are simultaneously the
exact thing asserted and a per-event diff a human can read; normalizing it for
legibility would only have put a second representation between the failure and
the cause.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, override
from unittest.mock import patch

import pytest

from gcmon.data import GCStatsInfo
from gcmon.exporters.chrome_trace_exporter import TraceExporter
from gcmon.monitor import EventsMonitor
from gcmon.monitor_loop import MonitorLoop
from gcmon.run_policy import Runner
from gcmon.stats import StreamingStats
from gcmon.target_process import ExternalProcess
from gcmon.wait_policy import no_wait_policy
from tests.test_loss_replay import MS, READ_COST_NS, RING_SIZES, capture_records, ring_at

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "monitored_run_chrome_trace.json"

TARGET_PID = 33328
CHILD_PID = 33512

# 600 ms is the period that puts both paths in the fixture. The target collects
# gen 0 twelve or thirteen times in one, against a ring of eleven slots, so
# almost every tick draws records *and* a loss span; gen 1 and gen 2 are
# outrun comfortably, so the same run carries generations that lose nothing.
TICK_INTERVAL_NS = 600 * MS

# How long after the tick instant each polled pid's read begins. The loop polls
# the target first and the children after it, and a real run's reads are spread
# across the tick rather than simultaneous; a slot per pid says so, and gives
# the two pids' spans distinguishable edges.
READ_SLOT_NS = 1 * MS

# The capture runs 11 s. Nine ticks cover the first five of them: long enough
# for every generation to collect and for gen 0 to go blind repeatedly, short
# enough that the fixture stays a file somebody can scroll.
TICKS = 9

# The child's collections, shifted this far later than the target's. Deliberately
# not a multiple of the tick, so the two pids' rings are never in step.
CHILD_SKEW_NS = 137 * MS

# Ticks the child is missing from the listing. It is polled on 0-3, gone on 4,
# and back on 5-8: the departure is what makes the loop prune, and the return is
# where the prune becomes visible, since a monitor that kept the child's cursors
# would have nothing new to say about the slots it had already read.
#
# One tick and not more. Gen 0 turns its whole ring over in a single 600 ms
# period, so a longer absence leaves nothing in the child's rings that it had
# read before it left, and the re-export the prune causes shrinks to gen 2's
# one collection.
CHILD_GONE = frozenset({4})

# The default. Buffering changes nothing about order or content, but a run whose
# flush points moved would be a different run, so the number is written down.
FLUSH_THRESHOLD = 1000


def _tree(tick: int) -> list[int]:
    """What `get_child_pids` answers on *tick*."""
    return [] if tick in CHILD_GONE else [CHILD_PID]


def _poll_order() -> list[list[int]]:
    """The pids the loop polls each tick, in the order it polls them.

    The loop's own expression, `[self._monitor.pid, *(child_pids or [])]`,
    written out here so the script and the run can be checked against each
    other -- `one_read` asserts pid by pid that they agree.
    """
    return [[TARGET_PID, *_tree(tick)] for tick in range(TICKS)]


def _script() -> tuple[list[int], list[tuple[int, int]]]:
    """The clock to hand out, and the reads to answer, for the whole run.

    Returns the `time.monotonic_ns` values in the order they will be asked for,
    and one `(pid, ts_read_start)` per `get_gc_stats` call. One tick is: the
    loop's single read for the tick instant, then two reads per polled pid, one
    either side of its `get_gc_stats`.
    """
    clock: list[int] = []
    reads: list[tuple[int, int]] = []
    for tick, pids in enumerate(_poll_order()):
        instant = tick * TICK_INTERVAL_NS
        clock.append(instant)
        for slot, pid in enumerate(pids, start=1):
            ts_read_start = instant + slot * READ_SLOT_NS
            clock.append(ts_read_start)
            clock.append(ts_read_start + READ_COST_NS)
            reads.append((pid, ts_read_start))
    return clock, reads


class _ScriptedClock:
    """`time.monotonic_ns`, spelled out in advance.

    Indexing rather than an iterator so overrunning raises `IndexError` naming
    the position, and so `spent` can be compared with the length afterwards. A
    change to the number of reads per tick has to fail here or in
    `test_the_clock_was_spent_exactly`; what it must never do is quietly read
    the next tick's instants and produce a plausible trace.
    """

    def __init__(self, instants: Sequence[int]) -> None:
        self._instants = instants
        self.spent = 0

    def __call__(self) -> int:
        instant = self._instants[self.spent]
        self.spent += 1
        return instant


class FixedRunner(Runner):
    """Exactly *ticks* ticks, and no clock behind them.

    `DurationRunner` reads `time.monotonic` to decide when to stop, which would
    put this machine's speed into the length of the run.
    """

    def __init__(self, ticks: int) -> None:
        self._ticks = ticks

    @override
    def run(self, stop: Callable[[], bool]) -> Generator[None, Any]:
        for _ in range(self._ticks):
            if stop():
                return
            yield


def _shifted(records: Mapping[int, list[GCStatsInfo]], skew_ns: int) -> dict[int, list[GCStatsInfo]]:
    """The capture again, every collection *skew_ns* later.

    A second process rather than a second capture: the child ran the same work
    the target did, a fraction of a second behind it. `duration` is cumulative
    pause and carries no instant, so it comes over untouched.
    """
    return {
        gen: [
            GCStatsInfo(
                gen=record.gen,
                iid=record.iid,
                ts_start=record.ts_start + skew_ns,
                ts_stop=record.ts_stop + skew_ns,
                heap_size=record.heap_size,
                collections=record.collections,
                collected=record.collected,
                uncollectable=record.uncollectable,
                candidates=record.candidates,
                duration=record.duration,
            )
            for record in gen_records
        ]
        for gen, gen_records in records.items()
    }


@dataclass(frozen=True)
class MonitoredRun:
    """One run of the loop, and what it is fair to ask about it afterwards."""

    trace: bytes
    clock_spent: int
    clock_scripted: int
    reads: list[tuple[int, int]]

    def events(self) -> list[dict[str, Any]]:
        """The trace parsed back, for the tests that ask what is in it rather
        than what it weighs. Nothing asserted through here stands in for the
        byte comparison -- these only say the fixture is worth having."""
        parsed: list[dict[str, Any]] = json.loads(self.trace)
        return parsed


def run_monitored(output: Path) -> MonitoredRun:
    """Drive the real loop over the capture and return the Chrome bytes."""
    truth = {
        TARGET_PID: capture_records(),
        CHILD_PID: _shifted(capture_records(), CHILD_SKEW_NS),
    }
    clock_instants, reads = _script()
    clock = _ScriptedClock(clock_instants)
    listings: Iterator[list[int]] = iter([_tree(tick) for tick in range(TICKS)])
    pending: Iterator[tuple[int, int]] = iter(reads)

    def one_listing(pid: int, recursive: bool = True) -> list[int]:
        assert pid == TARGET_PID, f"the tree was listed for {pid}, not the target"
        return next(listings)

    def one_read(pid: int, all_interpreters: bool = True) -> list[GCStatsInfo]:
        """The ring *pid* would have held when this read began.

        Asserting the pid rather than looking it up: the order the loop polls
        in is part of what this file pins, and a run that polled the child
        first would otherwise just get the child's ring and say nothing.
        """
        expected, ts_read_start = next(pending)
        assert pid == expected, f"expected a read of {expected}, got {pid}"
        records = truth[pid]
        return [slot for gen in sorted(records) for slot in ring_at(records[gen], gen, RING_SIZES[gen], ts_read_start)]

    exporter = TraceExporter(output_path=output, flush_threshold=FLUSH_THRESHOLD)
    monitor = EventsMonitor(
        ExternalProcess(pid=TARGET_PID),
        exporter,
        StreamingStats(),
        wait_policy_factory=no_wait_policy,
    )
    # `rate=0` so the between-tick wait returns at once, and no `rss_sampler`,
    # which would read this machine's memory once a second. `NoWaitPolicy` per
    # pid rather than `StartupTimeoutPolicy`, whose verdict on a failed poll is
    # a `time.monotonic` reading in seconds -- a clock this file does not own.
    # The policy factory goes to the monitor, which owns per-pid lifetime.
    loop = MonitorLoop(monitor, FixedRunner(TICKS), rate=0.0)

    with (
        patch("gcmon.monitor.get_child_pids", side_effect=one_listing),
        patch("gcmon.monitor.get_gc_stats", side_effect=one_read),
        # On the `time` module itself, not on either importer's namespace:
        # `monitor_loop` and `monitor` both reach it through `import time`, so
        # this one patch is what makes the tick instant and the read instants
        # come off the same scripted sequence.
        patch("time.monotonic_ns", clock),
    ):
        loop.run()

    monitor.stop()

    return MonitoredRun(
        trace=output.read_bytes(),
        clock_spent=clock.spent,
        clock_scripted=len(clock_instants),
        reads=reads,
    )


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> MonitoredRun:
    return run_monitored(tmp_path_factory.mktemp("trace") / "gcmon.json")


class TestTheScriptIsWorthPinning:
    """A guard over a run that never exercised anything would stay green
    forever. These say the run below is the one described in the docstring,
    so a fixture regenerated off a weakened script fails here first."""

    def test_more_than_one_pid_is_polled(self, run: MonitoredRun) -> None:
        assert {pid for pid, _ts in run.reads} == {TARGET_PID, CHILD_PID}

    def test_a_child_leaves_the_tree(self) -> None:
        """The prune path. Without a tick the child is missing from, the
        loop's `retain` and the policy deletion beside it never run."""
        polled = [set(pids) for pids in _poll_order()]

        assert any(CHILD_PID not in tick for tick in polled), "no tick prunes"
        assert polled[-1] == {TARGET_PID, CHILD_PID}, "the child never comes back"

    def test_both_pids_reach_the_trace(self, run: MonitoredRun) -> None:
        assert {event["pid"] for event in run.events()} == {TARGET_PID, CHILD_PID}

    def test_the_run_loses_records_as_well_as_drawing_them(self, run: MonitoredRun) -> None:
        """A poll period the ring always survives would pin the export path
        and leave ADR-0015's loss arithmetic out of the fixture entirely."""
        names = [event["name"] for event in run.events()]

        assert any(name.startswith("GC Pause(") for name in names)
        assert any(name.startswith("GC Loss(") for name in names)

    def test_the_clock_was_spent_exactly(self, run: MonitoredRun) -> None:
        """One read for the tick, two per polled pid, nothing left over. A
        change that reads the clock a different number of times per tick moves
        every timestamp downstream of it, and this is where it says so in one
        line rather than across the whole fixture diff."""
        assert run.clock_spent == run.clock_scripted


class TestTheTracesAreIdentical:
    def test_the_bytes_match_the_fixture(self, run: MonitoredRun) -> None:
        """The guard. See the module docstring before regenerating."""
        expected = FIXTURE.read_bytes()

        # Line by line first: both encodings are one JSON object per line, so
        # this is the assertion that prints a diff of the events that moved.
        # The byte comparison after it is what the claim actually rests on --
        # it also covers the line endings and the trailing newline, which
        # `splitlines` throws away.
        assert run.trace.decode().splitlines() == expected.decode().splitlines()
        assert run.trace == expected

    def test_a_second_run_produces_the_same_bytes(self, run: MonitoredRun, tmp_path: Path) -> None:
        """No machine clock anywhere. Cheap to state and the only thing that
        distinguishes a fixture from a record of one afternoon's timings."""
        again = run_monitored(tmp_path / "again.json")

        assert again.trace == run.trace


class TestTheChildLeavingIsVisible:
    """What the departure costs, in the trace rather than in a state dict.

    Asserting the monitor's `_pids` is empty after a prune proves the prune
    ran, not that it was correct. The observable consequence is here: a pid
    that leaves the tree loses its `collections` cursor, so the ring it comes
    back holding is entirely unseen and every record still in it is exported a
    second time. Duplicate slices are the price of the prune. If they vanish,
    the prune stopped happening -- and spec 0038's whole hazard is that the
    two halves of it stop agreeing.
    """

    def _pauses(self, run: MonitoredRun, pid: int) -> list[tuple[int, int]]:
        """`(generation, collections)` per GC Pause slice drawn for *pid*."""
        return [
            (int(event["args"]["generation"]), int(event["args"]["collections"]))
            for event in run.events()
            if event.get("ph") == "B" and str(event["name"]).startswith("GC Pause(") and event["pid"] == pid
        ]

    def test_the_returning_child_re_exports_the_ring_it_left_with(self, run: MonitoredRun) -> None:
        drawn = self._pauses(run, CHILD_PID)

        assert len(drawn) > len(set(drawn)), "the child came back with its cursor intact"

    def test_the_target_never_draws_a_collection_twice(self, run: MonitoredRun) -> None:
        """The control. The target is in the tree throughout, so nothing
        prunes it, and a duplicate on its rows would mean the cursor is being
        dropped by something other than the departure above."""
        drawn = self._pauses(run, TARGET_PID)

        assert len(drawn) == len(set(drawn))


def _regenerate() -> None:
    """Rewrite the fixture from a fresh run. See the module docstring."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        produced = run_monitored(Path(tmp) / "gcmon.json")

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_bytes(produced.trace)
    print(f"wrote {FIXTURE} ({len(produced.trace)} bytes, {len(produced.events())} events)")


if __name__ == "__main__":
    _regenerate()

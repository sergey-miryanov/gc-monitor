"""Tests for which records a poll treats as new.

``get_gc_stats`` returns the whole ring buffer every time, in slot order.
The two-poll fixture below is a verbatim capture from a CPython 3.15 target
allocating in a loop, polled twice 100 ms apart. Hand-written batches tend
to come out sorted, which hides the rotation that breaks naive dedup.
"""

from collections.abc import Sequence
from unittest.mock import patch

import pytest

from gcmon.data import GCStatsInfo
from gcmon.monitor import EventsMonitor
from gcmon.poll_status import PollStatus
from gcmon.stats import StreamingStats
from tests.helpers import MockExporter, create_mock_stats_item

PID = 12345

# (gen, collections, ts_start, ts_stop), in the slot order the extension
# returned them. Note gen 0 wraps after collections=476, and gen 2 holds two
# slots that were never written.
POLL_0: list[tuple[int, int, int, int]] = [
    (0, 473, 294787151995400, 294787152127300),
    (0, 474, 294787153982200, 294787154114900),
    (0, 475, 294787154366000, 294787154504600),
    (0, 476, 294787154776500, 294787154918900),
    (0, 466, 294787144021400, 294787144152700),
    (0, 467, 294787144418900, 294787144564500),
    (0, 468, 294787146641200, 294787146782800),
    (0, 469, 294787147038600, 294787147170500),
    (0, 470, 294787149158300, 294787149292000),
    (0, 471, 294787149543600, 294787149675000),
    (0, 472, 294787149927900, 294787150058800),
    (1, 42, 294787139971000, 294787140103000),
    (1, 43, 294787152383400, 294787152514700),
    (1, 41, 294787129573800, 294787129708500),
    (2, 0, 0, 0),
    (2, 1, 294786777619400, 294786778177300),
    (2, 0, 0, 0),
]

POLL_1: list[tuple[int, int, int, int]] = [
    (0, 561, 294787252319700, 294787252451800),
    (0, 562, 294787254273900, 294787254404700),
    (0, 563, 294787254660400, 294787254791100),
    (0, 553, 294787244879600, 294787245010000),
    (0, 554, 294787245265700, 294787245399800),
    (0, 555, 294787247335400, 294787247469400),
    (0, 556, 294787247727100, 294787247860100),
    (0, 557, 294787249221500, 294787249363900),
    (0, 558, 294787249616300, 294787249748300),
    (0, 559, 294787250008100, 294787250150200),
    (0, 560, 294787251930300, 294787252063300),
    (1, 51, 294787253889000, 294787254021200),
    (1, 49, 294787228540400, 294787228711600),
    (1, 50, 294787242504100, 294787242638800),
    (2, 0, 0, 0),
    (2, 1, 294786777619400, 294786778177300),
    (2, 0, 0, 0),
]


def build_batch(slots: Sequence[tuple[int, int, int, int]], iid: int = 0) -> list[GCStatsInfo]:
    return [
        create_mock_stats_item(gen=gen, collections=collections, ts_start=ts_start, ts_stop=ts_stop, iid=iid)
        for gen, collections, ts_start, ts_stop in slots
    ]


def seen(exporter: MockExporter) -> set[tuple[int, int]]:
    """The (gen, collections) pairs the exporter was handed."""
    return {(event.gen, event.collections) for event in exporter.events}


@pytest.fixture
def poll_0() -> list[GCStatsInfo]:
    return build_batch(POLL_0)


@pytest.fixture
def poll_1() -> list[GCStatsInfo]:
    return build_batch(POLL_1)


class TestFirstPoll:
    def test_ingests_every_written_slot(
        self, monitor: EventsMonitor, exporter: MockExporter, poll_0: list[GCStatsInfo]
    ) -> None:
        """A cursor advancing while walking the batch would stop at
        collections=476 and discard the seven gen-0 slots behind the wrap."""
        monitor._ingest(PID, poll_0)

        assert seen(exporter) == {(0, c) for c in range(466, 477)} | {(1, 41), (1, 42), (1, 43), (2, 1)}

    def test_older_generations_are_not_shadowed(
        self, monitor: EventsMonitor, exporter: MockExporter, poll_0: list[GCStatsInfo]
    ) -> None:
        """Each generation has its own ring and counter, so gen-1 records are
        new regardless of how recent the gen-0 slots preceding them are."""
        monitor._ingest(PID, poll_0)

        assert {event.collections for event in exporter.events if event.gen == 1} == {41, 42, 43}
        assert {event.collections for event in exporter.events if event.gen == 2} == {1}

    def test_unwritten_slots_are_skipped(
        self, monitor: EventsMonitor, exporter: MockExporter, poll_0: list[GCStatsInfo]
    ) -> None:
        monitor._ingest(PID, poll_0)

        assert len(exporter.events) == 15
        assert all(event.ts_start > 0 for event in exporter.events)

    def test_emitted_in_timestamp_order(
        self, monitor: EventsMonitor, exporter: MockExporter, poll_0: list[GCStatsInfo]
    ) -> None:
        monitor._ingest(PID, poll_0)

        timestamps = [event.ts_start for event in exporter.events]
        assert timestamps == sorted(timestamps)


class TestSubsequentPoll:
    def test_emits_only_records_not_seen_before(
        self, monitor: EventsMonitor, exporter: MockExporter, poll_0: list[GCStatsInfo], poll_1: list[GCStatsInfo]
    ) -> None:
        monitor._ingest(PID, poll_0)
        exporter.events.clear()

        monitor._ingest(PID, poll_1)

        assert seen(exporter) == {(0, c) for c in range(553, 564)} | {(1, 49), (1, 50), (1, 51)}

    def test_unchanged_generation_emits_nothing(
        self, monitor: EventsMonitor, exporter: MockExporter, poll_0: list[GCStatsInfo], poll_1: list[GCStatsInfo]
    ) -> None:
        """gen 2 did not collect between the polls, so its one written slot
        is unchanged and the monitor must not emit it twice."""
        monitor._ingest(PID, poll_0)
        exporter.events.clear()

        monitor._ingest(PID, poll_1)

        assert [event for event in exporter.events if event.gen == 2] == []

    def test_repeating_a_batch_emits_nothing(
        self, monitor: EventsMonitor, exporter: MockExporter, poll_0: list[GCStatsInfo]
    ) -> None:
        monitor._ingest(PID, poll_0)
        exporter.events.clear()

        monitor._ingest(PID, build_batch(POLL_0))

        assert exporter.events == []

    def test_duplicate_record_is_emitted_once(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        """Two slots reporting the same counter are one collection, a state
        the target holds while it copies a record forward."""
        item = create_mock_stats_item(gen=0, collections=7, ts_start=1_000, ts_stop=2_000)
        twin = create_mock_stats_item(gen=0, collections=7, ts_start=1_000, ts_stop=2_000)

        monitor._ingest(PID, [item, twin])

        assert len(exporter.events) == 1


class TestCursorScope:
    def test_pids_are_independent(
        self, monitor: EventsMonitor, exporter: MockExporter, poll_0: list[GCStatsInfo]
    ) -> None:
        monitor._ingest(PID, poll_0)
        exporter.events.clear()

        monitor._ingest(999, build_batch(POLL_0))

        assert len(exporter.events) == 15

    def test_interpreters_are_independent(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        """Sub-interpreters count from one, so their counters overlap and a
        shared cursor would drop whichever lagged."""
        first = create_mock_stats_item(gen=0, iid=0, collections=90, ts_start=9_000, ts_stop=9_500)
        second = create_mock_stats_item(gen=0, iid=1, collections=3, ts_start=3_000, ts_stop=3_500)

        monitor._ingest(PID, [first, second])

        assert seen(exporter) == {(0, 90), (0, 3)}

    def test_forget_drops_cursors_for_one_pid(
        self, monitor: EventsMonitor, exporter: MockExporter, poll_0: list[GCStatsInfo]
    ) -> None:
        monitor._ingest(PID, poll_0)
        monitor._ingest(999, build_batch(POLL_0))
        exporter.events.clear()

        monitor.forget(PID)
        monitor._ingest(PID, build_batch(POLL_0))
        monitor._ingest(999, build_batch(POLL_0))

        assert len(exporter.events) == 15

    def test_forget_is_safe_for_an_unknown_pid(self, monitor: EventsMonitor) -> None:
        monitor.forget(777)


class TestRetain:
    def test_a_stale_cursor_silences_a_reused_pid(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        """Why the loop has to drop cursors for pids that leave the process
        tree. Nothing here notices the counter restarting, so a reused pid
        stays silent until it climbs past its predecessor."""
        monitor._ingest(PID, [create_mock_stats_item(gen=0, collections=800, ts_start=8_000, ts_stop=8_500)])
        exporter.events.clear()

        monitor._ingest(PID, [create_mock_stats_item(gen=0, collections=2, ts_start=100, ts_stop=200)])

        assert exporter.events == []

    def test_retain_drops_pids_outside_the_tree(
        self, monitor: EventsMonitor, exporter: MockExporter, poll_0: list[GCStatsInfo]
    ) -> None:
        monitor._ingest(PID, poll_0)
        monitor._ingest(999, build_batch(POLL_0))
        exporter.events.clear()

        monitor.retain({PID})

        monitor._ingest(PID, build_batch(POLL_0))
        assert exporter.events == [], "the retained pid kept its cursors"
        monitor._ingest(999, build_batch(POLL_0))
        assert len(exporter.events) == 15, "the dropped pid started over"

    def test_retain_keeps_a_pid_with_no_cursors_yet(self, monitor: EventsMonitor) -> None:
        monitor.retain({PID, 999})


class TestPollIntegration:
    def test_poll_uses_the_cursor(
        self, monitor: EventsMonitor, exporter: MockExporter, poll_0: list[GCStatsInfo], poll_1: list[GCStatsInfo]
    ) -> None:
        """Two polls of the same target, through ``poll`` rather than
        ``_ingest``, so the read path is covered too."""
        with patch("gcmon.monitor.get_gc_stats", side_effect=[poll_0, poll_1]):
            assert monitor.poll(PID) == PollStatus.OK
            assert len(exporter.events) == 15

            assert monitor.poll(PID) == PollStatus.OK

        assert len(exporter.events) == 29

    def test_poll_reads_the_ring_geometry(
        self, monitor: EventsMonitor, stats: StreamingStats, poll_0: list[GCStatsInfo]
    ) -> None:
        """``poll`` holds the only unfiltered view of a read, so it is what
        hands the batch over. ``_ingest`` drops the empty slots the count needs.
        """
        with patch("gcmon.monitor.get_gc_stats", side_effect=[poll_0]):
            assert monitor.poll(PID) == PollStatus.OK

        assert [stats.ring_size(gen) for gen in (0, 1, 2)] == [11, 3, 3]

from collections.abc import Callable, Generator, Mapping, Sequence, Set
from typing import override
from unittest.mock import MagicMock, Mock, patch

import pytest

from gcmon.data import GCStatsInfo
from gcmon.monitor import EventsMonitor, PollReport
from gcmon.protocol import TGCStatsInfo
from gcmon.stats import StreamingStats
from gcmon.target_process import ExternalProcess
from gcmon.wait_policy import WaitPolicy, WaitPolicyFactory, no_wait_policy
from tests.helpers import MockExporter, create_mock_stats_item


@pytest.fixture
def mock_gc_stats() -> Generator[GCStatsInfo]:
    item = create_mock_stats_item(ts_start=1_000_000_000, ts_stop=1_005_000_000)
    with patch("gcmon.monitor.get_gc_stats", return_value=[item]):
        yield item


@pytest.fixture
def mock_stats_update(monitor: EventsMonitor) -> Generator[MagicMock]:
    with patch.object(monitor._stats, "update") as mock:
        yield mock


class TestEventsMonitorExtra:
    def test_get_child_pids(self, monitor: EventsMonitor) -> None:
        with patch("gcmon.monitor.get_child_pids", return_value=[999, 888]) as mock_get:
            children = monitor.get_child_pids()

        mock_get.assert_called_once_with(12345, recursive=True)
        assert children == [999, 888]

    def test_get_child_pids_exception_returns_none(self, monitor: EventsMonitor) -> None:
        """None rather than [], so the caller can tell a failed listing from
        a target with no children and skip pruning that tick."""
        with patch("gcmon.monitor.get_child_pids", side_effect=Exception("boom")) as mock_get:
            children = monitor.get_child_pids()

        mock_get.assert_called_once_with(12345, recursive=True)
        assert children is None

    def test_context_manager_enter_exit(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        assert monitor.is_enabled
        with monitor as m:
            assert m is monitor
            assert monitor.is_enabled
        assert not monitor.is_enabled

    def test_poll_updates_stats(
        self, monitor: EventsMonitor, mock_gc_stats: GCStatsInfo, mock_stats_update: MagicMock
    ) -> None:
        monitor.poll(12345)

        mock_stats_update.assert_called_once_with(12345, mock_gc_stats)

    def test_poll_skips_invalid_timestamp_event(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        item = create_mock_stats_item(ts_start=2_000, ts_stop=1_000)

        with patch("gcmon.monitor.get_gc_stats", return_value=[item]):
            monitor.poll(12345)

        assert exporter.events == []

    def test_poll_skips_equal_timestamp_event(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        item = create_mock_stats_item(ts_start=1_000, ts_stop=1_000)

        with patch("gcmon.monitor.get_gc_stats", return_value=[item]):
            monitor.poll(12345)

        assert exporter.events == []

    def test_poll_tracks_last_timestamp_per_pid(self, monitor: EventsMonitor, exporter: MockExporter) -> None:
        """A child PID's events are not suppressed by a later timestamp seen on
        another PID. One monitor polls the target and every child, and their
        event streams interleave in time."""
        per_pid = {
            12345: [create_mock_stats_item(collections=50, ts_start=5_000, ts_stop=5_100)],
            999: [
                create_mock_stats_item(collections=7, ts_start=4_000, ts_stop=4_100),
                create_mock_stats_item(collections=8, ts_start=6_000, ts_stop=6_100),
            ],
        }

        with patch("gcmon.monitor.get_gc_stats", side_effect=lambda pid, **_: per_pid[pid]):
            monitor.poll(12345)
            monitor.poll(999)

        assert [e.ts_start for e in exporter.events] == [5_000, 4_000, 6_000]

    def test_poll_still_skips_already_seen_timestamps_for_same_pid(
        self, monitor: EventsMonitor, exporter: MockExporter
    ) -> None:
        item = create_mock_stats_item(ts_start=5_000, ts_stop=5_100)

        with patch("gcmon.monitor.get_gc_stats", return_value=[item]):
            monitor.poll(12345)
            monitor.poll(12345)

        assert [e.ts_start for e in exporter.events] == [5_000]


# --------------------------------------------------------------------------
# One tick, one call
# --------------------------------------------------------------------------
#
# Spec 0038. Everything gcmon carries from one poll of a pid to the next --
# the ring cursors, the poll instant ADR-0015's arithmetic runs on, and the
# wait policy that decides when the pid is finished -- has one owner and is
# pruned once, against one set.
#
# These drive `tick` and assert on its report and on what reached the
# exporter. Asserting that the monitor's state dicts are empty after a prune
# would prove the prune ran, not that it was correct; the observable
# consequence of getting it wrong is a `GC Loss` window for collections that
# never happened.


def _ring(*collections: int, gen: int = 0, iid: int = 0) -> list[GCStatsInfo]:
    """A whole ring buffer holding one finished record per counter given.

    A poll returns the ring, not the new records in it, so this is what
    `get_gc_stats` answers -- deciding which of them is unseen is the
    monitor's job and the thing under test.
    """
    return [
        create_mock_stats_item(
            gen=gen,
            iid=iid,
            collections=c,
            ts_start=c * 1_000,
            ts_stop=c * 1_000 + 100,
            duration=c * 0.001,
        )
        for c in collections
    ]


def _policy(*verdicts: bool) -> Mock:
    """A wait policy answering *verdicts* in order, one per poll it judges."""
    return Mock(spec=WaitPolicy, **{"wait.side_effect": list(verdicts)})


def _monitor(
    exporter: MockExporter,
    *,
    pid: int = 12345,
    wait_policy_factory: WaitPolicyFactory = no_wait_policy,
    is_pid_enabled: Callable[[int], bool] | None = None,
) -> EventsMonitor:
    """A monitor wired the way the monitoring command wires one."""
    return EventsMonitor(
        ExternalProcess(pid=pid),
        exporter,
        StreamingStats(),
        wait_policy_factory=wait_policy_factory,
        is_pid_enabled=is_pid_enabled,
    )


Batch = Sequence[GCStatsInfo] | Exception


# What the caller's clock reads on each tick. Spelled out so a test can name
# the instant a liveness observation was stamped with.
TICK_NS = 1_000_000_000


def _never_stops() -> bool:
    """A tick nothing interrupts, which is what most of these tests want.

    `tick` requires a cancel check rather than defaulting to this, since its
    one production caller always has a stop event to hand. It lives here
    because the tests are the only thing that ever wanted the default.
    """
    return False


def _drive(
    monitor: EventsMonitor,
    listings: Sequence[list[int] | Exception],
    rings: Mapping[int, Sequence[Batch]],
    stop: Callable[[], bool] = _never_stops,
) -> list[PollReport]:
    """One tick per entry in *listings*; one report back per tick.

    *listings* is what the child listing answers each tick, an exception
    entry standing for a listing that failed -- which the monitor turns into
    the ``None`` that means "no answer", not into an empty tree.

    *rings* gives the whole ring buffer each successive poll of a pid
    returns, so a pid polled three times needs three entries and a pid that
    is expected never to be polled needs none: a poll that was not supposed
    to happen raises `KeyError` here rather than passing quietly.
    """
    pending = {pid: iter(batches) for pid, batches in rings.items()}

    def read(pid: int, all_interpreters: bool = True) -> list[GCStatsInfo]:
        batch = next(pending[pid])
        if isinstance(batch, Exception):
            raise batch
        return list(batch)

    reports: list[PollReport] = []
    with (
        patch("gcmon.monitor.get_child_pids", side_effect=list(listings)),
        patch("gcmon.monitor.get_gc_stats", side_effect=read),
    ):
        for tick, _ in enumerate(listings, start=1):
            now_ns = tick * TICK_NS
            reports.append(monitor.tick(now_ns, stop))

    return reports


class TestTheReport:
    """What a tick answers: who was alive, and whether to keep going."""

    def test_names_the_pids_that_answered(self, exporter: MockExporter) -> None:
        reports = _drive(
            _monitor(exporter),
            listings=[[999]],
            rings={12345: [_ring(1)], 999: [_ring(1)]},
        )

        assert reports[0].live_pids == frozenset({12345, 999})

    def test_a_pid_that_could_not_be_read_is_not_live(self, exporter: MockExporter) -> None:
        """Only ``PollStatus.OK`` is evidence a process was there. A failed
        read is evidence of nothing."""
        reports = _drive(
            _monitor(exporter),
            listings=[[999]],
            rings={12345: [_ring(1)], 999: [RuntimeError("no such process")]},
        )

        assert reports[0].live_pids == frozenset({12345})

    def test_the_live_set_is_frozen(self, exporter: MockExporter) -> None:
        """Nothing downstream mutates it -- the sampler iterates it and the
        exporter folds it into a min/max -- so it is handed over frozen."""
        reports = _drive(_monitor(exporter), listings=[[]], rings={12345: [_ring(1)]})

        assert isinstance(reports[0].live_pids, frozenset)

    def test_keep_running_while_a_policy_still_waits(self, exporter: MockExporter) -> None:
        reports = _drive(
            _monitor(exporter, wait_policy_factory=Mock(side_effect=[_policy(True)])),
            listings=[[]],
            rings={12345: [_ring(1)]},
        )

        assert reports[0].keep_running

    def test_keep_running_is_false_once_every_policy_has_given_up(self, exporter: MockExporter) -> None:
        reports = _drive(
            _monitor(exporter, wait_policy_factory=Mock(side_effect=[_policy(False), _policy(False)])),
            listings=[[999]],
            rings={12345: [_ring(1)], 999: [_ring(1)]},
        )

        assert not reports[0].keep_running

    def test_one_policy_still_waiting_is_enough(self, exporter: MockExporter) -> None:
        reports = _drive(
            _monitor(exporter, wait_policy_factory=Mock(side_effect=[_policy(False), _policy(True)])),
            listings=[[999]],
            rings={12345: [_ring(1)], 999: [_ring(1)]},
        )

        assert reports[0].keep_running

    def test_keep_running_is_false_when_nothing_was_polled_at_all(self, exporter: MockExporter) -> None:
        """No policy answered, so none is holding the loop open. The tick
        before this one is what kept the run alive; this one ends it."""
        reports = _drive(
            _monitor(exporter, is_pid_enabled=lambda pid: False),
            listings=[[999]],
            rings={},
        )

        assert not reports[0].keep_running
        assert reports[0].live_pids == frozenset()


class TestTheControlPlaneVerdict:
    def test_a_suppressed_pid_is_not_polled_and_is_not_live(self, exporter: MockExporter) -> None:
        """A pid the control server disabled is never read, so it is never
        observed. `rings` has no entry for it, so a poll would raise."""
        reports = _drive(
            _monitor(exporter, is_pid_enabled=lambda pid: pid != 999),
            listings=[[999]],
            rings={12345: [_ring(1)]},
        )

        assert reports[0].live_pids == frozenset({12345})
        assert 999 not in exporter.events_by_pid


class TestTheStopCheck:
    def test_a_tick_gives_up_between_pids(self, exporter: MockExporter) -> None:
        """Shutdown does not have to wait out a whole process tree. The
        event behind the check belongs to the caller; the monitor reads it."""
        answers = iter([False, True])
        reports = _drive(
            _monitor(exporter),
            listings=[[999]],
            rings={12345: [_ring(1)]},
            stop=lambda: next(answers),
        )

        assert reports[0].live_pids == frozenset({12345})
        assert 999 not in exporter.events_by_pid


class TestARecycledPidStartsFromNothing:
    """Spec 0038's hazard, seen where it would actually surface.

    A pid that leaves the process tree loses its cursor and its poll instant.
    Whatever the OS gives that number to next is a different process with its
    own `collections` counter, and comparing the two is what invents a loss
    window for collections that never happened -- a span an operator would
    read as a gcmon measurement rather than a gcmon bug.
    """

    def test_the_first_poll_back_emits_records_and_no_loss_window(self, exporter: MockExporter) -> None:
        _drive(
            _monitor(exporter),
            listings=[[999], [], [999]],
            rings={
                12345: [_ring(1), _ring(1), _ring(1)],
                # The pid comes back holding a counter from a process that has
                # nothing to do with the one that left.
                999: [_ring(1, 2), _ring(300, 301)],
            },
        )

        assert exporter.loss_events == []
        assert [event.collections for event in exporter.events_by_pid[999]] == [1, 2, 300, 301]

    def test_the_same_ring_without_a_departure_does_open_one(self, exporter: MockExporter) -> None:
        """The control, and the reason the assertion above has teeth. Held in
        the tree the whole time, the same counters are one process's, and the
        jump between them is a real gap that ADR-0015 is right to report."""
        _drive(
            _monitor(exporter),
            listings=[[999], [999], [999]],
            rings={
                12345: [_ring(1), _ring(1), _ring(1)],
                999: [_ring(1, 2), _ring(1, 2), _ring(300, 301)],
            },
        )

        lost = [gen.lost_count for _pid, msg in exporter.loss_events for gen in msg.gens]
        assert lost == [297]


class TestOnePruneOverOneSet:
    """The ring state and the wait policy have one lifetime between them.

    They were pruned in two modules against two expressions of the same child
    set. These say both go, and go together.
    """

    def test_a_pid_that_leaves_the_tree_loses_its_ring_state(self, exporter: MockExporter) -> None:
        """Visible as a re-export: a monitor that kept the cursor would have
        nothing to say about slots it had already read."""
        _drive(
            _monitor(exporter),
            listings=[[999], [], [999]],
            rings={
                12345: [_ring(1), _ring(1), _ring(1)],
                999: [_ring(1, 2), _ring(1, 2)],
            },
        )

        assert [event.collections for event in exporter.events_by_pid[999]] == [1, 2, 1, 2]

    def test_a_pid_that_leaves_the_tree_loses_its_policy_too(self, exporter: MockExporter) -> None:
        """Same tick, same set. A policy outliving the cursor would judge a
        new process on what the old one did."""
        factory = Mock(side_effect=[_policy(True, True, True), _policy(True), _policy(True)])
        _drive(
            _monitor(exporter, wait_policy_factory=factory),
            listings=[[999], [], [999]],
            rings={
                12345: [_ring(1), _ring(1), _ring(1)],
                999: [_ring(1, 2), _ring(1, 2)],
            },
        )

        # 12345 once, then 999 twice: once on first sight, once on return.
        assert factory.call_count == 3

    def test_a_failed_listing_prunes_nothing(self, exporter: MockExporter) -> None:
        """``None`` from the listing means "no answer", not "no children".
        Reading it as an empty tree would drop the cursors of every live child
        and re-export their whole rings on the next poll."""
        _drive(
            _monitor(exporter),
            listings=[[999], Exception("cannot enumerate"), [999]],
            rings={
                12345: [_ring(1), _ring(1), _ring(1)],
                # Polled on the first and third ticks; the second cannot see it.
                999: [_ring(1, 2), _ring(1, 2)],
            },
        )

        assert [event.collections for event in exporter.events_by_pid[999]] == [1, 2]


class TestProcessLiveness:
    """A successful read is the only evidence gcmon has that a process was
    still there, and for a process that never collects it is the only evidence
    of any kind. The tick that produced it is what reports it. See ADR-0011.
    """

    def test_reported_once_per_tick_with_the_whole_live_set(self, exporter: MockExporter) -> None:
        """One call and one lock acquisition per tick, not per pid."""
        _drive(
            _monitor(exporter),
            listings=[[999, 888]],
            rings={12345: [_ring(1)], 999: [_ring(1)], 888: [_ring(1)]},
        )

        assert exporter.liveness == [(frozenset({12345, 999, 888}), TICK_NS)]

    def test_reported_every_tick(self, exporter: MockExporter) -> None:
        _drive(
            _monitor(exporter),
            listings=[[999], [888]],
            rings={12345: [_ring(1), _ring(1)], 999: [_ring(1)], 888: [_ring(1)]},
        )

        assert [pids for pids, _ts in exporter.liveness] == [
            frozenset({12345, 999}),
            frozenset({12345, 888}),
        ]

    def test_stamped_with_the_instant_the_tick_was_given(self, exporter: MockExporter) -> None:
        _drive(
            _monitor(exporter),
            listings=[[], []],
            rings={12345: [_ring(1), _ring(1)]},
        )

        assert [ts for _pids, ts in exporter.liveness] == [TICK_NS, 2 * TICK_NS]

    def test_a_pid_that_could_not_be_read_is_not_reported(self, exporter: MockExporter) -> None:
        _drive(
            _monitor(exporter),
            listings=[[999]],
            rings={12345: [_ring(1)], 999: [RuntimeError("gone")]},
        )

        assert exporter.liveness == [(frozenset({12345}), TICK_NS)]

    def test_a_suppressed_pid_is_not_reported(self, exporter: MockExporter) -> None:
        """Never polled, so never observed. Its span keeps whatever it had."""
        _drive(
            _monitor(exporter, is_pid_enabled=lambda pid: pid != 999),
            listings=[[999]],
            rings={12345: [_ring(1)]},
        )

        assert exporter.liveness == [(frozenset({12345}), TICK_NS)]

    def test_not_reported_when_nothing_answered(self, exporter: MockExporter) -> None:
        """An empty set would widen no span, so the call is skipped rather
        than made with nothing in it."""
        _drive(
            _monitor(exporter),
            listings=[[]],
            rings={12345: [RuntimeError("gone")]},
        )

        assert exporter.liveness == []

    def test_reported_after_the_records_the_same_tick_produced(self) -> None:
        """ADR-0011: after the poll phase, never during it. That ordering is
        what leaves a batch crossing `flush_threshold` mid-poll still able to
        emit a rank-less process descriptor -- which the ADR records, and which
        stays true for the same reason now that both happen in one call."""
        exporter = _OrderedExporter()

        _drive(
            _monitor(exporter),
            listings=[[999]],
            rings={12345: [_ring(1)], 999: [_ring(1)]},
        )

        assert exporter.order == ["record", "record", "liveness"]


class _OrderedExporter(MockExporter):
    """Records what reached the exporter, in the order it arrived."""

    def __init__(self) -> None:
        super().__init__()
        self.order: list[str] = []

    @override
    def add_event(self, pid: int, item: TGCStatsInfo) -> None:
        self.order.append("record")
        super().add_event(pid, item)

    @override
    def add_process_liveness(self, pids: Set[int], ts_ns: int) -> None:
        self.order.append("liveness")
        super().add_process_liveness(pids, ts_ns)


class TestTheExporterIsNotReachableThroughTheMonitor:
    def test_there_is_no_exporter_property(self, exporter: MockExporter) -> None:
        """It existed for one caller, the loop's liveness call, which is now
        inside. The only code emitting to the exporter is the code that owns
        what it emits."""
        assert not hasattr(_monitor(exporter), "exporter")


class TestAPidThePolicyGaveUpOn:
    """Two halves of one rule, and they pull in opposite directions: the
    cursors go, the policy stays. A test that only counts policies would pass
    with the cursors kept, and one that only watched the cursors would pass
    with the policy replaced.
    """

    def test_no_fresh_policy_replaces_it(self, exporter: MockExporter) -> None:
        """A replacement would not have seen the pid alive, so it would answer
        "still starting" to every further invalid poll and hold the run open
        for a whole startup timeout.

        Three ticks, not two: the policy gives up on the second, so the third
        is the only one that can observe whether a replacement was built. With
        two, the factory could not have been called again either way and the
        count held for the wrong reason.
        """
        factory = Mock(side_effect=[_policy(True, True, True), _policy(True, False, False)])
        _drive(
            _monitor(exporter, wait_policy_factory=factory),
            listings=[[999], [999], [999]],
            rings={
                12345: [_ring(1), _ring(1), _ring(1)],
                999: [_ring(1), RuntimeError("gone"), RuntimeError("still gone")],
            },
        )

        # One each for 12345 and 999, and none built to replace 999.
        assert factory.call_count == 2

    def test_its_cursors_go(self, exporter: MockExporter) -> None:
        """The other half. A pid still listed as a child whose policy has quit
        is finished as far as gcmon is concerned, so what comes back under that
        number is a new process and must not be measured against the old one's
        counter. Kept cursors would answer a fresh low `collections` with a
        loss window for collections that never happened.
        """
        factory = Mock(side_effect=[_policy(True, True, True), _policy(True, False, True)])
        _drive(
            _monitor(exporter, wait_policy_factory=factory),
            listings=[[999], [999], [999]],
            rings={
                12345: [_ring(1), _ring(1), _ring(1)],
                # Reaches 300, dies, and the number comes back on a process
                # counting from 1 again.
                999: [_ring(299, 300), RuntimeError("gone"), _ring(1, 2)],
            },
        )

        assert exporter.loss_events == []
        assert [event.collections for event in exporter.events_by_pid[999]] == [299, 300, 1, 2]

    def test_the_run_can_end_on_it(self, exporter: MockExporter) -> None:
        """Every policy giving up in one tick is how a run ends: nothing
        answered, so nothing is live and nothing holds the run open."""
        factory = Mock(side_effect=[_policy(False), _policy(False)])
        reports = _drive(
            _monitor(exporter, wait_policy_factory=factory),
            listings=[[999]],
            rings={12345: [RuntimeError("gone")], 999: [RuntimeError("gone too")]},
        )

        assert reports[0].live_pids == frozenset()
        assert not reports[0].keep_running
        assert exporter.liveness == [], "an observation of nothing widens no span"


class TestNoWaitPolicyThroughAWholeTick:
    """`no_wait_policy` is what the eight poll-only monitors in this suite
    configure, so what it does to a tick is worth stating once."""

    def test_a_successful_poll_keeps_the_run_open(self, exporter: MockExporter) -> None:
        reports = _drive(_monitor(exporter), listings=[[]], rings={12345: [_ring(1)]})

        assert reports[0].live_pids == frozenset({12345})
        assert reports[0].keep_running

    def test_a_failed_poll_ends_it(self, exporter: MockExporter) -> None:
        """Which is why anything monitoring a target that may still be
        initializing configures `StartupTimeoutPolicy` instead."""
        reports = _drive(
            _monitor(exporter),
            listings=[[]],
            rings={12345: [RuntimeError("still starting")]},
        )

        assert not reports[0].keep_running


class TestTheConstructorRefusesToPickAPolicy:
    def test_a_monitor_cannot_be_built_without_one(
        self, exporter: MockExporter, process: ExternalProcess, stats: StreamingStats
    ) -> None:
        """Omitting it used to yield a monitor that gave up on the first failed
        poll and reported that as an orderly finish."""
        with pytest.raises(TypeError):
            EventsMonitor(process, exporter, stats)  # type: ignore[call-arg]
